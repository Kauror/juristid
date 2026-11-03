"""Contract A — repeated register values survive every step (owner's brief of 2026-11-02).

Parse → apply → chips → carried inputs → sort and page links → named views →
the shared selection an export reads. Plus the four new windows: actually sent,
next-step date, last activity and last modification.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters import register_filters, register_selection
from app.matters.models import Matter
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import DatePrecision
from tests import factories
from tests.synthetic_statistics import _document, _version

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
TALLINN = ZoneInfo("Europe/Tallinn")
TODAY = timezone.localdate()


def _titles(response) -> list[str]:
    return [matter.title for matter in response.context["page"].object_list]


def _q(**values) -> QueryDict:
    query = QueryDict(mutable=True)
    for key, value in values.items():
        query.setlist(key, value if isinstance(value, list) else [value])
    return query


def _sent(matter: Matter, *, on, restricted: bool = False, status=SubmissionStatus.SENT):
    document = _document(matter, f"{matter.title} lõplik")
    if restricted:
        document.visibility_override = Visibility.RESTRICTED
        document.save(update_fields=["visibility_override"])
    version = _version(document, filename="arvamus.pdf", payload=f"mv {matter.pk} {on}")
    return Submission.objects.create(
        matter=matter,
        title="Arvamus",
        status=status,
        sent_at=datetime.combine(on, time(9), tzinfo=TALLINN),
        final_version=version,
        visibility_override=Visibility.RESTRICTED if restricted else "",
    )


@pytest.fixture
def stages():
    return factories.StageFactory(key="mv-a", label_et="MV A"), factories.StageFactory(
        key="mv-b", label_et="MV B"
    )


# ---------------------------------------------------------------------------
# OR within a dimension, AND across dimensions
# ---------------------------------------------------------------------------


def test_a_single_value_link_written_before_still_means_what_it_meant(signed_in, stages):
    first, second = stages
    factories.MatterFactory(title="Esimene", stage=first)
    factories.MatterFactory(title="Teine", stage=second)

    response = signed_in.get(REGISTER, {"hetkeseis": first.key})

    assert _titles(response) == ["Esimene"]


def test_two_values_of_one_dimension_are_alternatives(signed_in, stages):
    first, second = stages
    factories.MatterFactory(title="Esimene", stage=first)
    factories.MatterFactory(title="Teine", stage=second)
    factories.MatterFactory(title="Kolmas")

    response = signed_in.get(f"{REGISTER}?hetkeseis={first.key}&hetkeseis={second.key}")

    assert sorted(_titles(response)) == ["Esimene", "Teine"]


def test_different_dimensions_narrow_each_other(signed_in, specialist, stages):
    first, second = stages
    factories.MatterFactory(title="Minu A", stage=first, owner=specialist)
    factories.MatterFactory(title="Minu B", stage=second, owner=specialist)
    factories.MatterFactory(title="Kellegi A", stage=first)

    response = signed_in.get(
        f"{REGISTER}?hetkeseis={first.key}&hetkeseis={second.key}&vastutaja={specialist.pk}"
    )

    assert sorted(_titles(response)) == ["Minu A", "Minu B"]


def test_missing_is_a_value_of_its_own_not_all(signed_in, specialist):
    factories.MatterFactory(title="Vastutajata", owner=None)
    factories.MatterFactory(title="Minu", owner=specialist)
    factories.MatterFactory(title="Kellegi teise", owner=factories.UserFactory())

    only_missing = signed_in.get(REGISTER, {"vastutaja": "puudub"})
    either = signed_in.get(f"{REGISTER}?vastutaja=puudub&vastutaja={specialist.pk}")
    nothing_chosen = signed_in.get(REGISTER)

    assert _titles(only_missing) == ["Vastutajata"]
    assert sorted(_titles(either)) == ["Minu", "Vastutajata"]
    assert len(_titles(nothing_chosen)) == 3


def test_a_teema_without_menetlusliik_can_be_chosen(signed_in):
    # Bug hunt 2026-11-03: most Teemad carry no menetlusliik, and the panel
    # offered every kind but «Määramata», so none of them could be listed.
    factories.MatterFactory(title="Liigita", track="")
    factories.MatterFactory(title="Koja algatus", track="KODA_INITIATIVE")

    panel = signed_in.get(REGISTER).content.decode()
    group = panel.split('<legend class="field__label">Menetlusliik</legend>', 1)[1]
    group = group.split("</fieldset>", 1)[0]
    assert 'value="puudub"' in group and "Määramata" in group
    assert _titles(signed_in.get(REGISTER, {"menetlusliik": "puudub"})) == ["Liigita"]
    both = signed_in.get(f"{REGISTER}?menetlusliik=puudub&menetlusliik=KODA_INITIATIVE")
    assert sorted(_titles(both)) == ["Koja algatus", "Liigita"]


@pytest.mark.parametrize(
    "address",
    [
        "?vastutaja=puudub&vastutaja=mitte-uuid",
        "?arvamus=saadetud&arvamus=midagi-muud",
    ],
)
def test_one_unreadable_value_empties_the_list_rather_than_widening_it(signed_in, address):
    factories.MatterFactory(title="Midagi", owner=None)

    response = signed_in.get(REGISTER + address)

    assert response.status_code == 200
    assert _titles(response) == []


def test_two_valdkonnad_do_not_list_a_teema_twice(signed_in):
    first = factories.PolicyAreaFactory(key="mv-tooturg", name_et="Tööturg")
    second = factories.PolicyAreaFactory(key="mv-maksud", name_et="Maksud")
    matter = factories.MatterFactory(title="Mõlemas")
    matter.policy_areas.add(first, second)
    factories.MatterFactory(title="Valdkonnata")

    response = signed_in.get(f"{REGISTER}?valdkond={first.key}&valdkond={second.key}")
    with_missing = signed_in.get(f"{REGISTER}?valdkond={first.key}&valdkond=puudub")

    assert _titles(response) == ["Mõlemas"]
    assert response.context["total"] == 1
    assert sorted(_titles(with_missing)) == ["Mõlemas", "Valdkonnata"]


def test_the_legal_instrument_filter_reads_the_governed_vocabulary(signed_in):
    from app.taxonomy.models import LegalInstrumentType

    law = LegalInstrumentType.objects.create(key="mv-seadus", label_et="MV seadus")
    decree = LegalInstrumentType.objects.create(key="mv-maarus", label_et="MV määrus")
    by_law = factories.MatterFactory(title="Seadus")
    by_law.legal_instruments.add(law)
    by_decree = factories.MatterFactory(title="Määrus")
    by_decree.legal_instruments.add(decree)
    factories.MatterFactory(title="Liigita")

    both = signed_in.get(f"{REGISTER}?oigusakt={law.key}&oigusakt={decree.key}")
    missing = signed_in.get(REGISTER, {"oigusakt": "puudub"})

    assert sorted(_titles(both)) == ["Määrus", "Seadus"]
    assert _titles(missing) == ["Liigita"]
    assert "MV seadus" in [chip["value"] for chip in both.context["active_filters"]]


# ---------------------------------------------------------------------------
# Every serialization keeps every value
# ---------------------------------------------------------------------------


def test_each_value_is_its_own_chip_and_its_cross_removes_only_itself(signed_in, stages):
    first, second = stages
    factories.MatterFactory(stage=first)

    response = signed_in.get(f"{REGISTER}?hetkeseis={first.key}&hetkeseis={second.key}&leht=1")
    chips = [chip for chip in response.context["active_filters"] if chip["name"] == "hetkeseis"]

    assert [chip["value"] for chip in chips] == ["MV A", "MV B"]
    remaining = QueryDict(chips[0]["remove_query"])
    assert remaining.getlist("hetkeseis") == [second.key]
    assert "leht" not in remaining


def test_the_search_box_carries_every_value(signed_in, stages):
    first, second = stages

    response = signed_in.get(f"{REGISTER}?hetkeseis={first.key}&hetkeseis={second.key}")
    carried = response.context["carried_params"]

    assert ("hetkeseis", first.key) in carried and ("hetkeseis", second.key) in carried
    body = response.content.decode()
    for key in (first.key, second.key):
        assert f'type="hidden" name="hetkeseis" value="{key}"' in body


def test_sort_links_page_links_and_page_sizes_keep_every_value(signed_in, stages):
    first, second = stages
    for _ in range(14):
        factories.MatterFactory(stage=first)

    response = signed_in.get(
        f"{REGISTER}?hetkeseis={first.key}&hetkeseis={second.key}&kaupa=12&veerud=viide"
    )

    page_link = QueryDict(response.context["query_string"])
    sort_link = QueryDict(response.context["column_sorts"]["kuupaev"]["query"])
    size_link = QueryDict(response.context["page_size_options"][0]["query"])
    for link in (page_link, sort_link, size_link):
        assert link.getlist("hetkeseis") == [first.key, second.key]
        assert link.get("veerud") == "viide"


def test_the_panel_keeps_page_size_and_columns_and_ticks_every_value(signed_in, stages):
    first, second = stages

    body = signed_in.get(
        f"{REGISTER}?hetkeseis={first.key}&hetkeseis={second.key}&kaupa=50&veerud=viide,valdkond"
    ).content.decode()

    assert re.search(rf'name="hetkeseis" value="{first.key}" checked', body)
    assert re.search(rf'name="hetkeseis" value="{second.key}" checked', body)
    assert '<input type="hidden" name="kaupa" value="50">' in body
    assert '<input type="hidden" name="veerud" value="viide,valdkond">' in body


def test_as_text_and_the_population_keep_every_value(specialist, stages):
    first, second = stages
    factories.MatterFactory(stage=first)
    factories.MatterFactory(stage=second)

    text = register_filters.as_text({"hetkeseis": [first.key, second.key], "suletud": 2026})
    counted = register_filters.register_population(
        specialist, _q(hetkeseis=[first.key, second.key])
    ).count()

    assert text.getlist("hetkeseis") == [first.key, second.key]
    assert text.get("suletud") == "2026"
    assert counted == 2


def test_a_named_view_lights_up_whatever_order_its_values_arrive_in(signed_in):
    response = signed_in.get(f"{REGISTER}?vastutaja=puudub&olek=avatud")
    active = [view.key for view in response.context["saved_views"] if view.active]

    assert active == ["vastutajata"]
    more = signed_in.get(f"{REGISTER}?vastutaja=puudub&vastutaja=puudub")
    assert [view.key for view in more.context["saved_views"] if view.active] == ["vastutajata"]


def test_register_url_repeats_a_dimension_for_a_list():
    url = register_filters.register_url(hetkeseis=["a", "b"], olek="avatud")

    assert "hetkeseis=a&hetkeseis=b" in url and "olek=avatud" in url


# ---------------------------------------------------------------------------
# The shared selection is the table
# ---------------------------------------------------------------------------


def test_the_shared_selection_is_every_row_the_table_pages_through_in_order(
    signed_in, specialist, stages
):
    first, second = stages
    for index in range(30):
        factories.MatterFactory(title=f"Rida {index:02}", stage=first if index % 2 else second)
    address = f"hetkeseis={first.key}&hetkeseis={second.key}&jarjestus=title&kaupa=12"

    shown = []
    for page in (1, 2, 3):
        shown += _titles(signed_in.get(f"{REGISTER}?{address}&leht={page}"))
    rows = register_selection.register_rows(specialist, QueryDict(address))

    assert [matter.title for matter in rows] == shown
    assert len(shown) == 30


def test_free_text_participates_in_the_shared_selection(specialist):
    from app.search.indexing import indexable_matters, refresh_matters

    wanted = factories.MatterFactory(title="Pakendiaktsiis")
    factories.MatterFactory(title="Midagi muud")
    refresh_matters(indexable_matters())

    rows = register_selection.register_rows(specialist, QueryDict("q=pakendiaktsiis"))

    assert [matter.pk for matter in rows] == [wanted.pk]


def test_an_unknown_sort_is_the_default_never_an_order_by_from_the_address(specialist):
    selection = register_selection.selection_from(
        specialist, QueryDict("jarjestus=-owner__password")
    )

    assert selection.sort == register_selection.DEFAULT_SORT


# ---------------------------------------------------------------------------
# The new windows
# ---------------------------------------------------------------------------


def test_sent_window_counts_actual_sends_and_never_a_draft(signed_in):
    sent = factories.MatterFactory(title="Saadetud")
    _sent(sent, on=TODAY - timedelta(days=3))
    withdrawn = factories.MatterFactory(title="Saadetud ja tagasi võetud")
    _sent(withdrawn, on=TODAY - timedelta(days=2), status=SubmissionStatus.WITHDRAWN)
    draft = factories.MatterFactory(title="Mustand")
    factories.SubmissionFactory(matter=draft)
    old = factories.MatterFactory(title="Vana")
    _sent(old, on=TODAY - timedelta(days=60))

    start = (TODAY - timedelta(days=7)).strftime("%d.%m.%Y")
    response = signed_in.get(REGISTER, {"saadetud_alates": start, "olek": "koik"})

    assert sorted(_titles(response)) == ["Saadetud", "Saadetud ja tagasi võetud"]


def test_the_sent_window_reads_sends_as_the_reader_may_see_them(client):
    """Scoped by `Submission.objects.visible_to` before the window applies.

    Since docs/adr/0181 every business reader sees restricted child records, so
    the reader who sees nothing is the administrator without a business role.
    """
    matter = factories.MatterFactory(title="Piiratud saatmine")
    _sent(matter, on=TODAY, restricted=True)
    client.force_login(factories.AdministratorFactory())

    response = client.get(REGISTER, {"saadetud_alates": TODAY.strftime("%d.%m.%Y")})

    assert response.status_code in (200, 302, 403, 404)
    if response.status_code == 200:
        assert _titles(response) == []


def test_a_bounded_step_window_holds_a_day_never_a_month(signed_in):
    dated = factories.MatterFactory(title="Päevaga")
    factories.NextActionFactory(
        matter=dated, target_date=TODAY + timedelta(days=2), date_precision=DatePrecision.EXACT
    )
    month = factories.MatterFactory(title="Kuuga")
    factories.NextActionFactory(
        matter=month, target_date=TODAY.replace(day=1), date_precision=DatePrecision.MONTH
    )

    response = signed_in.get(
        REGISTER,
        {
            "samm_alates": (TODAY - timedelta(days=40)).strftime("%d.%m.%Y"),
            "samm_kuni": (TODAY + timedelta(days=40)).strftime("%d.%m.%Y"),
        },
    )

    assert _titles(response) == ["Päevaga"]


def test_last_modification_is_not_last_activity(signed_in):
    matter = factories.MatterFactory(title="Vana töö, uus parandus")
    factories.EntryFactory(
        matter=matter, occurred_at=timezone.now() - timedelta(days=100), kind="MEETING"
    )
    Matter.objects.filter(pk=matter.pk).update(updated_at=timezone.now())
    since = (TODAY - timedelta(days=10)).strftime("%d.%m.%Y")

    modified = signed_in.get(REGISTER, {"muudetud_alates": since})
    active = signed_in.get(REGISTER, {"viimane_alates": since})

    assert "Vana töö, uus parandus" in _titles(modified)
    assert "Vana töö, uus parandus" not in _titles(active)


@pytest.mark.parametrize(
    ("start", "end"),
    [("saabus_alates", "saabus_kuni"), ("saadetud_alates", "saadetud_kuni")],
)
def test_an_inverted_range_empties_the_list_and_says_why(signed_in, start, end):
    factories.MatterFactory(received_date=TODAY)

    response = signed_in.get(
        REGISTER,
        {start: TODAY.strftime("%d.%m.%Y"), end: (TODAY - timedelta(days=5)).strftime("%d.%m.%Y")},
    )

    assert _titles(response) == []
    assert register_filters.RANGE_ERROR in response.content.decode()


def test_the_activity_window_and_activity_sort_compose(signed_in):
    matter = factories.MatterFactory(title="Hiljutine")
    factories.EntryFactory(matter=matter, occurred_at=timezone.now(), kind="MEETING")

    response = signed_in.get(
        REGISTER,
        {
            "viimane_alates": (TODAY - timedelta(days=3)).strftime("%d.%m.%Y"),
            "jarjestus": register_selection.ACTIVITY_SORT_NEWEST,
        },
    )

    assert response.status_code == 200
    assert "Hiljutine" in _titles(response)
