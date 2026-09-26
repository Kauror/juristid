"""One happy path and one refusal for each correction view nothing else drives (ENG-038).

`test_business_write_boundary` sends only *forbidden* actors to these views, so
the permission gate was all anything asserted about them. What a permitted save
stores, and what a bad one refuses, was untested, and a regression in either
would reach a lawyer first.

Reachability, revalidated on current main:

* `edit/cancel` for an important date and a commencement, and `edit/reject` for a
  work victory, are linked from the intelligence partials the Matter's facts
  render — still product paths.
* `documents:add_version` is linked from no template: URL-reachable only. It is
  tested as it stands, not restored; whether it survives is ENG-050's decision.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.documents.models import DocumentVersion
from app.intelligence.enums import EffectiveDateKind, FactStatus, WorkVictoryStatus
from app.intelligence.services import (
    add_effective_date,
    add_important_date,
    add_work_victory_candidate,
    cancel_effective_date,
)
from app.workflow.enums import DatePrecision
from tests import factories
from tests import synthetic_corpus as corpus
from tests.test_file_intake_robustness import _store_verbatim

pytestmark = pytest.mark.django_db


def _url(name: str, matter, record) -> str:
    return reverse(f"intelligence:{name}", kwargs={"matter_id": matter.pk, "pk": record.pk})


def _post_edit(client, url: str, data: dict):
    """Open the form, then save it with the revision it was rendered with.

    What a browser does. Posting without the token is the stale-tab case, which
    the conflict guard answers 409 — tested where that guard is tested
    (`tests/test_matter_write_serialization.py`), not here.
    """
    revision = client.get(url).context["form"].initial.get("revision", "")
    return client.post(url, {**data, "revision": revision})


# --- Oluline tähtaeg --------------------------------------------------------


def _important(matter, actor):
    return add_important_date(
        matter=matter,
        title="Kooskõlastusring",
        date_value=date(2030, 5, 1),
        period_end=date(2030, 5, 1),
        actor=actor,
    )


def test_editing_an_important_date_stores_the_change(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = _important(matter, specialist)

    response = _post_edit(
        signed_in,
        _url("edit_important_date", matter, record),
        {"title": "Teine ring", "precision": "EXACT", "exact_date": "2030-06-02"},
    )

    assert response.status_code == 302
    record.refresh_from_db()
    assert (record.title, record.date_value) == ("Teine ring", date(2030, 6, 2))


def test_editing_an_important_date_refuses_a_period_with_no_year(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = _important(matter, specialist)

    response = _post_edit(
        signed_in,
        _url("edit_important_date", matter, record),
        {"title": "Teine ring", "precision": "QUARTER", "quarter": "2"},
    )

    assert response.status_code == 400
    record.refresh_from_db()
    assert record.title == "Kooskõlastusring"


def test_cancelling_an_important_date_twice_is_refused(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = _important(matter, specialist)
    url = _url("cancel_important_date", matter, record)

    assert signed_in.post(url, {"reason": "Loobuti"}).status_code == 302
    record.refresh_from_db()
    assert record.status == FactStatus.CANCELLED

    assert signed_in.post(url, {"reason": "Uuesti"}).status_code == 400


# --- Jõustumine -------------------------------------------------------------


def _commencement(matter, actor):
    return add_effective_date(
        matter=matter,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=date(2027, 1, 1),
        period_end=date(2027, 1, 1),
        description="Põhiosa",
        actor=actor,
    )


def test_editing_a_commencement_stores_the_change(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = _commencement(matter, specialist)

    response = _post_edit(
        signed_in,
        _url("edit_effective_date", matter, record),
        {
            "kind": EffectiveDateKind.KNOWN_DATE,
            "precision": "EXACT",
            "exact_date": "2027-07-01",
            "description": "Põhiosa",
        },
    )

    assert response.status_code == 302
    record.refresh_from_db()
    assert record.date_value == date(2027, 7, 1)


def test_editing_a_commencement_refuses_a_date_on_a_general_order(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = _commencement(matter, specialist)

    response = _post_edit(
        signed_in,
        _url("edit_effective_date", matter, record),
        {
            "kind": EffectiveDateKind.GENERAL_ORDER,
            "precision": "EXACT",
            "exact_date": "2027-07-01",
            "description": "Põhiosa",
        },
    )

    assert response.status_code == 400
    record.refresh_from_db()
    assert record.kind == EffectiveDateKind.KNOWN_DATE
    assert record.date_value == date(2027, 1, 1)


def test_cancelling_a_commencement_marks_it_and_a_second_cancel_is_refused(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = _commencement(matter, specialist)
    url = _url("cancel_effective_date", matter, record)

    assert signed_in.post(url, {"reason": "Seadus muutus"}).status_code == 302
    record.refresh_from_db()
    assert record.status == FactStatus.CANCELLED

    assert signed_in.post(url, {"reason": "Uuesti"}).status_code == 400


def test_a_cancelled_commencement_cannot_be_edited(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = _commencement(matter, specialist)
    cancel_effective_date(record=record, actor=specialist, reason="Tühistatud")

    response = _post_edit(
        signed_in,
        _url("edit_effective_date", matter, record),
        {
            "kind": EffectiveDateKind.KNOWN_DATE,
            "precision": "EXACT",
            "exact_date": "2027-07-01",
            "description": "Põhiosa",
        },
    )

    assert response.status_code == 400
    record.refresh_from_db()
    assert record.date_value == date(2027, 1, 1)


# --- Töövõit ----------------------------------------------------------------


def test_editing_a_work_victory_stores_the_change(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = add_work_victory_candidate(matter=matter, title="Ettepanek", actor=specialist)

    response = _post_edit(
        signed_in,
        _url("edit_work_victory", matter, record),
        {"title": "Ettepanek arvestati", "precision": "YEAR", "year": "2026"},
    )

    assert response.status_code == 302
    record.refresh_from_db()
    assert record.title == "Ettepanek arvestati"
    assert record.date_precision == DatePrecision.YEAR


def test_editing_a_work_victory_refuses_a_quarter_with_no_year(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = add_work_victory_candidate(matter=matter, title="Ettepanek", actor=specialist)

    response = _post_edit(
        signed_in,
        _url("edit_work_victory", matter, record),
        {"title": "Muudetud", "precision": "QUARTER", "quarter": "1"},
    )

    assert response.status_code == 400
    record.refresh_from_db()
    assert record.title == "Ettepanek"


def test_the_department_head_rejects_a_candidate(client, specialist, department_head):
    matter = factories.MatterFactory(owner=specialist)
    record = add_work_victory_candidate(matter=matter, title="Ettepanek", actor=specialist)
    client.force_login(department_head)

    response = client.post(_url("reject_work_victory", matter, record), {"reason": "Ei arvestatud"})

    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == WorkVictoryStatus.NOT_REALIZED


def test_a_specialist_may_not_reject_a_candidate(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = add_work_victory_candidate(matter=matter, title="Ettepanek", actor=specialist)

    response = signed_in.post(_url("reject_work_victory", matter, record), {"reason": "Ei"})

    assert response.status_code == 403
    record.refresh_from_db()
    assert record.status == WorkVictoryStatus.CANDIDATE


# --- A further version of a document ----------------------------------------


def test_a_further_version_is_added_and_the_first_is_kept(
    client, specialist, normal_matter, evidence_root
):
    client.force_login(specialist)
    document = _store_verbatim(normal_matter, "olemas.pdf", specialist)
    first = document.current_version

    response = client.post(
        reverse("documents:add_version", kwargs={"pk": document.pk}),
        {"upload": SimpleUploadedFile("uus.pdf", corpus.text_pdf(["Teine versioon"]))},
    )

    assert response.status_code == 302
    assert [str(m) for m in get_messages(response.wsgi_request)] == ["Uus versioon on salvestatud."]
    versions = list(DocumentVersion.objects.filter(document=document).order_by("version_number"))
    assert [v.version_number for v in versions] == [1, 2]
    assert versions[0].pk == first.pk


def test_a_further_version_with_no_file_asks_for_one(
    client, specialist, normal_matter, evidence_root
):
    client.force_login(specialist)
    document = _store_verbatim(normal_matter, "olemas.pdf", specialist)

    response = client.post(reverse("documents:add_version", kwargs={"pk": document.pk}), {})

    assert response.status_code == 302
    assert [str(m) for m in get_messages(response.wsgi_request)] == ["Vali fail."]
    assert DocumentVersion.objects.filter(document=document).count() == 1
