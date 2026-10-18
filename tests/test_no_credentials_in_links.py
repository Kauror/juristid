"""No user name or password in a link a person pastes now (owner decision D10).

The owner decided on 2026-10-17 (DC-G2): **every paste-now link field refuses
userinfo**, on creation and on correction, with the one Estonian sentence
`Link ei tohi sisaldada kasutajanime ega parooli.`; the historical-register
importer keeps accepting it, and rows already stored are not rewritten
(docs/adr/0085 §2, amendment of 2026-10-17).

`Muu`, `Ülevaade / uudis` and `Menetluse link` already refused it and have their
own tests. This module covers the doors that tolerated it:

* `+ Kaasamine` and its `Muuda` — `Ülevaate link`, `Smaily link`, `Alchemer link`;
* `Lisa tagasiside` and `Muuda` on a recorded position — `Link`;
* the Dokumendid tab's working document — `SharePointi aadress` (no correction
  door exists);
* `Jõustumine` and `Töövõit`, add and `Muuda` — `Ametlik allikas` / `Viide`.

And the two halves of «not rewritten»: a correction that leaves a stored
historical credential-bearing link untouched still saves and keeps it, while a
changed one is refused; and the register importer still writes one.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.core.web_addresses import LINK_HAS_CREDENTIALS, normalize_web_address
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.services import link_working_document
from app.intelligence import services as intelligence
from app.intelligence.enums import EffectiveDateKind
from app.intelligence.models import MatterEffectiveDate, MatterWorkVictory
from app.matters.enums import EngagementKind, ExternalPositionProvenance
from app.matters.models import MatterEngagement, MatterExternalPosition
from app.matters.services import (
    add_engagement,
    correct_engagement,
    engagement_revision_token,
    external_position_revision,
)
from app.matters.workspace import add_matter_external_position
from tests.refusals import refused

pytestmark = pytest.mark.django_db

#: The shape the audit used: a user name and a password in the authority.
CREDENTIALED = "https://u:p@example.org/x"
#: A second one, so «changed» is distinguishable from «untouched».
OTHER_CREDENTIALED = "https://kasutaja:parool@example.org/teine"
PLAIN = "https://example.org/avalik"

ENGAGEMENT_LINKS = ("url", "smaily_url", "alchemer_url")
#: `+ Kaasamine` names the round's page `website_url`; the column is `url`.
PANEL_FIELD = {"url": "website_url", "smaily_url": "smaily_url", "alchemer_url": "alchemer_url"}


def _htmx_post(client, url, data):
    return client.post(url, data, headers={"HX-Request": "true"})


# -- the shared rule ----------------------------------------------------------


def test_the_shared_rule_keeps_an_unchanged_stored_address_and_nothing_else():
    assert (
        normalize_web_address(
            CREDENTIALED, max_length=None, reject_credentials=True, stored=CREDENTIALED
        )
        == CREDENTIALED
    )
    with refused(LINK_HAS_CREDENTIALS):
        normalize_web_address(
            OTHER_CREDENTIALED, max_length=None, reject_credentials=True, stored=CREDENTIALED
        )
    with refused(LINK_HAS_CREDENTIALS):
        normalize_web_address(CREDENTIALED, max_length=None, reject_credentials=True)


# -- Kaasamine ------------------------------------------------------------------


def _round(matter, actor, **links) -> MatterEngagement:
    return add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        occurred_on=timezone.localdate() - dt.timedelta(days=3),
        actor=actor,
        **links,
    )


def _store_historically(engagement, **columns) -> None:
    """A row as the register importer left it, before D10 — written below the rule."""
    MatterEngagement.objects.filter(pk=engagement.pk).update(**columns)
    engagement.refresh_from_db()


def _correction(engagement, **changes) -> dict[str, str]:
    """Everything `Muuda` posts, filled from the record the way the page fills it."""
    engagement.refresh_from_db()
    return {
        "kind": engagement.kind,
        "title": engagement.title,
        "response_count": "",
        "url": engagement.url,
        "smaily_url": engagement.smaily_url,
        "alchemer_url": engagement.alchemer_url,
        "note": engagement.note,
        "occurred_on": format_estonian_date(engagement.occurred_on),
        "feedback_deadline": "",
        "feedback_received": engagement.feedback_received,
        "revision": engagement_revision_token(engagement),
        **changes,
    }


def _correct(client, engagement, **changes):
    return _htmx_post(
        client,
        reverse(
            "matters:update_engagement",
            kwargs={"pk": engagement.matter_id, "engagement_id": engagement.pk},
        ),
        _correction(engagement, **changes),
    )


@pytest.mark.parametrize("column", ENGAGEMENT_LINKS)
def test_kaasamine_panel_refuses_a_credentialed_link(signed_in, normal_matter, column):
    response = _htmx_post(
        signed_in,
        reverse("matters:add_engagement_compact", kwargs={"pk": normal_matter.pk}),
        {"audience": "liikmed", PANEL_FIELD[column]: CREDENTIALED},
    )

    assert response.status_code == 400
    assert LINK_HAS_CREDENTIALS in response.content.decode()
    assert not MatterEngagement.objects.filter(matter=normal_matter).exists()


@pytest.mark.parametrize("column", ENGAGEMENT_LINKS)
def test_kaasamine_correction_refuses_a_newly_typed_credentialed_link(
    signed_in, normal_matter, specialist, column
):
    engagement = _round(normal_matter, specialist, **{column: PLAIN})
    token = engagement_revision_token(engagement)

    response = _correct(signed_in, engagement, **{column: CREDENTIALED})

    assert response.status_code == 400
    assert LINK_HAS_CREDENTIALS in response.content.decode()
    engagement.refresh_from_db()
    assert getattr(engagement, column) == PLAIN
    assert engagement_revision_token(engagement) == token


@pytest.mark.parametrize("column", ENGAGEMENT_LINKS)
def test_kaasamine_correction_keeps_an_untouched_historical_credentialed_link(
    signed_in, normal_matter, specialist, column
):
    engagement = _round(normal_matter, specialist)
    _store_historically(engagement, **{column: CREDENTIALED})

    response = _correct(signed_in, engagement, title="Parandatud pealkiri")

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.title == "Parandatud pealkiri"
    assert getattr(engagement, column) == CREDENTIALED


@pytest.mark.parametrize("column", ENGAGEMENT_LINKS)
def test_kaasamine_correction_refuses_a_historical_link_changed_into_another(
    signed_in, normal_matter, specialist, column
):
    engagement = _round(normal_matter, specialist)
    _store_historically(engagement, **{column: CREDENTIALED})

    response = _correct(signed_in, engagement, **{column: OTHER_CREDENTIALED})

    assert response.status_code == 400
    assert LINK_HAS_CREDENTIALS in response.content.decode()
    engagement.refresh_from_db()
    assert getattr(engagement, column) == CREDENTIALED


@pytest.mark.parametrize("column", ENGAGEMENT_LINKS)
def test_kaasamine_services_refuse_it_below_the_form(normal_matter, specialist, column):
    """The service is the boundary: a crafted POST or a new caller meets the same rule."""
    with refused(LINK_HAS_CREDENTIALS):
        _round(normal_matter, specialist, **{column: CREDENTIALED})

    engagement = _round(normal_matter, specialist)
    with refused(LINK_HAS_CREDENTIALS):
        correct_engagement(engagement=engagement, actor=specialist, **{column: CREDENTIALED})

    _store_historically(engagement, **{column: CREDENTIALED})
    kept = correct_engagement(
        engagement=engagement, actor=specialist, title="Uus", **{column: CREDENTIALED}
    )
    assert getattr(kept, column) == CREDENTIALED


def test_the_historical_register_door_still_accepts_it(normal_matter, specialist):
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Registri kampaania",
        url=CREDENTIALED,
        lifecycle_tracked=False,
        from_historical_register=True,
        actor=specialist,
    )
    assert engagement.url == CREDENTIALED


# -- Lisa tagasiside and Muuda on a position ------------------------------------


def _position(matter, organisation, actor, url=PLAIN) -> MatterExternalPosition:
    return add_matter_external_position(
        matter=matter,
        author=actor,
        organisation=organisation,
        provenance=ExternalPositionProvenance.DISCOVERED.value,
        url=url,
        summary="Seisukoht.",
    ).record


def _correct_position(client, position, *, url, summary="Seisukoht."):
    return _htmx_post(
        client,
        reverse(
            "matters:update_external_position",
            kwargs={"pk": position.matter_id, "position_id": position.pk},
        ),
        {
            "organisation": str(position.organisation_id),
            "url": url,
            "summary": summary,
            "position_precision": "EXACT",
            "revision": external_position_revision(position),
        },
    )


def test_lisa_tagasiside_refuses_a_credentialed_link(signed_in, normal_matter, organisation):
    response = _htmx_post(
        signed_in,
        reverse("matters:add_engagement_reply", kwargs={"pk": normal_matter.pk}),
        {
            "organisation": str(organisation.pk),
            "provenance": ExternalPositionProvenance.RECEIVED.value,
            "summary": "Liige toetab.",
            "url": CREDENTIALED,
        },
    )

    assert response.status_code == 400
    assert LINK_HAS_CREDENTIALS in response.content.decode()
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_the_position_service_refuses_it_below_the_form(normal_matter, organisation, specialist):
    with refused(LINK_HAS_CREDENTIALS):
        _position(normal_matter, organisation, specialist, url=CREDENTIALED)
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_position_correction_refuses_a_newly_typed_credentialed_link(
    signed_in, normal_matter, organisation, specialist
):
    position = _position(normal_matter, organisation, specialist)

    response = _correct_position(signed_in, position, url=CREDENTIALED)

    assert response.status_code == 400
    assert LINK_HAS_CREDENTIALS in response.content.decode()
    position.refresh_from_db()
    assert position.url == PLAIN


def test_position_correction_keeps_an_untouched_historical_credentialed_link(
    signed_in, normal_matter, organisation, specialist
):
    """A `LEGACY` row out of the historical register, corrected for its wording."""
    position = _position(normal_matter, organisation, specialist)
    MatterExternalPosition.objects.filter(pk=position.pk).update(
        url=CREDENTIALED, provenance=ExternalPositionProvenance.LEGACY.value
    )
    position.refresh_from_db()

    response = _correct_position(signed_in, position, url=CREDENTIALED, summary="Parandatud.")

    assert response.status_code == 200
    position.refresh_from_db()
    assert position.summary == "Parandatud."
    assert position.url == CREDENTIALED

    changed = _correct_position(signed_in, position, url=OTHER_CREDENTIALED)
    assert changed.status_code == 400
    assert LINK_HAS_CREDENTIALS in changed.content.decode()
    position.refresh_from_db()
    assert position.url == CREDENTIALED


# -- the working document -------------------------------------------------------


def test_the_working_document_refuses_a_credentialed_address(signed_in, normal_matter):
    response = signed_in.post(
        reverse("matters:add_working_document", kwargs={"pk": normal_matter.pk}),
        {"title": "Töödokument.docx", "web_url": CREDENTIALED, "site_path": ""},
        follow=True,
    )

    assert LINK_HAS_CREDENTIALS in response.content.decode()
    assert not Document.objects.filter(
        matter=normal_matter, role=DocumentRole.WORKING_DOCUMENT
    ).exists()
    with refused(LINK_HAS_CREDENTIALS):
        link_working_document(matter=normal_matter, title="Töödokument", web_url=CREDENTIALED)


# -- Jõustumine and Töövõit -----------------------------------------------------


def _effective_date(matter, actor, source_url=PLAIN) -> MatterEffectiveDate:
    return intelligence.add_effective_date(
        matter=matter,
        kind=EffectiveDateKind.GENERAL_ORDER,
        description="põhiosa",
        source_url=source_url,
        actor=actor,
    )


def _victory(matter, actor, source_url=PLAIN) -> MatterWorkVictory:
    return intelligence.add_confirmed_work_victory(
        matter=matter, title="Ettepanek arvestati", source_url=source_url, actor=actor
    )


def _edit_effective(client, record, *, source_url, description="põhiosa"):
    return client.post(
        reverse(
            "intelligence:edit_effective_date",
            kwargs={"matter_id": record.matter_id, "pk": record.pk},
        ),
        {
            "revision": intelligence.fact_revision(record),
            "kind": EffectiveDateKind.GENERAL_ORDER,
            "description": description,
            "source_url": source_url,
        },
    )


def _edit_victory(client, record, *, source_url, title="Ettepanek arvestati"):
    return client.post(
        reverse(
            "intelligence:edit_work_victory",
            kwargs={"matter_id": record.matter_id, "pk": record.pk},
        ),
        {
            "revision": intelligence.fact_revision(record),
            "title": title,
            "precision": "",
            "source_url": source_url,
        },
    )


def test_joustumine_add_refuses_a_credentialed_source(signed_in, normal_matter):
    response = signed_in.post(
        reverse("intelligence:add_effective_date", kwargs={"matter_id": normal_matter.pk}),
        {
            "kind": EffectiveDateKind.GENERAL_ORDER,
            "description": "põhiosa",
            "source_url": CREDENTIALED,
        },
    )

    assert response.status_code == 400
    assert LINK_HAS_CREDENTIALS in response.content.decode()
    assert not MatterEffectiveDate.objects.filter(matter=normal_matter).exists()


def test_toovoit_add_refuses_a_credentialed_source(signed_in, normal_matter):
    response = signed_in.post(
        reverse("intelligence:add_work_victory", kwargs={"matter_id": normal_matter.pk}),
        {"title": "Ettepanek arvestati", "precision": "", "source_url": CREDENTIALED},
    )

    assert response.status_code == 400
    assert LINK_HAS_CREDENTIALS in response.content.decode()
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()


def test_joustumine_correction_refuses_new_and_keeps_untouched(
    signed_in, normal_matter, specialist
):
    record = _effective_date(normal_matter, specialist)

    refused_response = _edit_effective(signed_in, record, source_url=CREDENTIALED)
    assert refused_response.status_code == 400
    assert LINK_HAS_CREDENTIALS in refused_response.content.decode()
    record.refresh_from_db()
    assert record.source_url == PLAIN

    MatterEffectiveDate.objects.filter(pk=record.pk).update(source_url=CREDENTIALED)
    record.refresh_from_db()
    kept = _edit_effective(signed_in, record, source_url=CREDENTIALED, description="osad")
    assert kept.status_code == 302
    record.refresh_from_db()
    assert record.description == "osad"
    assert record.source_url == CREDENTIALED


def test_toovoit_correction_refuses_new_and_keeps_untouched(signed_in, normal_matter, specialist):
    record = _victory(normal_matter, specialist)

    refused_response = _edit_victory(signed_in, record, source_url=CREDENTIALED)
    assert refused_response.status_code == 400
    assert LINK_HAS_CREDENTIALS in refused_response.content.decode()
    record.refresh_from_db()
    assert record.source_url == PLAIN

    MatterWorkVictory.objects.filter(pk=record.pk).update(source_url=CREDENTIALED)
    record.refresh_from_db()
    kept = _edit_victory(signed_in, record, source_url=CREDENTIALED, title="Parandatud võit")
    assert kept.status_code == 302
    record.refresh_from_db()
    assert record.title == "Parandatud võit"
    assert record.source_url == CREDENTIALED


def test_the_intelligence_services_refuse_it_below_the_form(normal_matter, specialist):
    with refused(LINK_HAS_CREDENTIALS):
        _effective_date(normal_matter, specialist, source_url=CREDENTIALED)
    with refused(LINK_HAS_CREDENTIALS):
        _victory(normal_matter, specialist, source_url=CREDENTIALED)

    effective = _effective_date(normal_matter, specialist)
    with refused(LINK_HAS_CREDENTIALS):
        intelligence.update_effective_date(
            record=effective,
            kind=EffectiveDateKind.GENERAL_ORDER,
            date_value=None,
            period_end=None,
            date_precision="EXACT",
            source_url=CREDENTIALED,
            actor=specialist,
        )
    victory = _victory(normal_matter, specialist)
    with refused(LINK_HAS_CREDENTIALS):
        intelligence.update_work_victory(
            record=victory, title="Võit", source_url=CREDENTIALED, actor=specialist
        )


def test_a_machine_or_import_candidate_keeps_its_source_as_given(normal_matter):
    """`add_work_victory_candidate` is the importer's and the machine's door."""
    candidate = intelligence.add_work_victory_candidate(
        matter=normal_matter, title="Registri võit", source_url=CREDENTIALED
    )
    assert candidate.source_url == CREDENTIALED
