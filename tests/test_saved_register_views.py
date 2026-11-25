"""«Minu vaated» — a person's own named Teemad views (UX-007, docs/adr/0222).

What a stored view may hold (a canonical register query and a name), whose it
is (the sender's, and nobody else may read, rename or remove it), what it costs
(one aggregate statement for all of a person's views), and the promise every
chip keeps: the count it shows is the rows its link opens.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qsl

import pytest
from django.db import IntegrityError, transaction
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from app.core.errors import DomainError
from app.matters import saved_views
from app.matters.models import SavedRegisterView
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
SAVE = reverse("matters:save_view")


def _rename(view) -> str:
    return reverse("matters:rename_view", kwargs={"pk": view.pk})


def _remove(view) -> str:
    return reverse("matters:remove_view", kwargs={"pk": view.pk})


def _total(client, query: str) -> int:
    return client.get(f"{REGISTER}?{query}").context["total"]


# ---------------------------------------------------------------------------
# What is stored
# ---------------------------------------------------------------------------


def test_the_stored_query_is_canonical_and_holds_only_register_parameters():
    params = QueryDict(
        "vastutaja=b&leht=4&hetkeseis=x&vastutaja=a&q=pakend&tundmatu=1&asutus_otsing=min"
        "&olek=avatud&veerud=pealkiri,saabus&kaupa=30&aasta="
    )

    query = saved_views.canonical_query(params)

    assert query == (
        "hetkeseis=x&kaupa=30&olek=avatud&q=pakend&vastutaja=a&vastutaja=b&veerud=pealkiri%2Csaabus"
    )
    _, dropped = saved_views.canonical_pairs(params)
    assert dropped == ["asutus_otsing", "tundmatu"], "unknown names are reported, not kept"


def test_a_blank_olek_is_a_choice_and_survives():
    assert saved_views.canonical_query(QueryDict("olek=&vastutaja=puudub")) == (
        "olek=&vastutaja=puudub"
    )


def test_two_orders_of_one_address_are_one_stored_view():
    first = saved_views.canonical_query(QueryDict("olek=avatud&vastutaja=a&hetkeseis=x"))
    second = saved_views.canonical_query(QueryDict("hetkeseis=x&vastutaja=a&olek=avatud&leht=2"))
    assert first == second


def test_saving_keeps_a_name_and_a_query_and_nothing_else(specialist):
    factories.MatterFactory(owner=specialist, title="Näidisseadus")

    view = saved_views.save_view(
        user=specialist, name="  Minu   eelnõud ", params=QueryDict("ulatus=minu&olek=avatud")
    )

    assert view.name == "Minu eelnõud"
    assert view.query == "olek=avatud&ulatus=minu"
    stored = {field.name for field in SavedRegisterView._meta.get_fields()}
    assert stored == {"id", "created_at", "updated_at", "user", "name", "query", "position"}, (
        "a view stores an address — never rows, counts or an access decision"
    )


# ---------------------------------------------------------------------------
# Naming and the limit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("", "Anna vaatele nimi"),
        ("   ", "Anna vaatele nimi"),
        ("x" * 61, "kuni 60 märki"),
        ("Rida\u0007kaks", "juhtmärke"),
    ],
)
def test_a_bad_name_is_refused_in_words(specialist, name, words):
    with pytest.raises(DomainError, match=words):
        saved_views.save_view(user=specialist, name=name, params=QueryDict("olek=koik"))
    assert not SavedRegisterView.objects.exists()


def test_a_name_is_one_per_person_whatever_its_case(specialist, other_specialist):
    saved_views.save_view(user=specialist, name="Kiire", params=QueryDict("olek=koik"))

    with pytest.raises(DomainError, match="on sul juba olemas"):
        saved_views.save_view(user=specialist, name="KIIRE", params=QueryDict("olek=avatud"))
    # A colleague's name space is their own.
    saved_views.save_view(user=other_specialist, name="kiire", params=QueryDict("olek=koik"))

    with pytest.raises(IntegrityError), transaction.atomic():
        SavedRegisterView.objects.create(user=specialist, name="kIIRE", query="olek=koik")


def test_a_name_clash_the_check_missed_is_still_a_refusal_in_words(specialist, monkeypatch):
    """The database's one-name guard, reached past the service's own check (as a
    racing writer would), answers in words and leaves the transaction usable."""
    saved_views.save_view(user=specialist, name="Kiire", params=QueryDict("olek=koik"))
    other = saved_views.save_view(user=specialist, name="Teine", params=QueryDict("olek=avatud"))
    monkeypatch.setattr(saved_views, "_name_taken", lambda *args, **kwargs: False)

    with pytest.raises(DomainError, match="on sul juba olemas"):
        saved_views.save_view(user=specialist, name="KIIRE", params=QueryDict("q=sona"))
    with pytest.raises(DomainError, match="on sul juba olemas"):
        saved_views.rename_view(view=other, name="kiire")

    assert SavedRegisterView.objects.filter(user=specialist).count() == 2
    other.refresh_from_db()
    assert other.name == "Teine"


def test_the_bare_register_is_not_a_view(specialist):
    with pytest.raises(DomainError, match="vaikevaade"):
        saved_views.save_view(user=specialist, name="Kõik", params=QueryDict("leht=3"))


def test_twenty_views_is_the_most_a_person_keeps(specialist, other_specialist):
    for number in range(saved_views.MAX_VIEWS):
        saved_views.save_view(
            user=specialist, name=f"Vaade {number}", params=QueryDict(f"aasta={2000 + number}")
        )

    with pytest.raises(DomainError, match="juba 20 vaadet"):
        saved_views.save_view(user=specialist, name="Üks veel", params=QueryDict("olek=koik"))
    assert SavedRegisterView.objects.filter(user=specialist).count() == 20
    # The limit is per person.
    saved_views.save_view(user=other_specialist, name="Üks veel", params=QueryDict("olek=koik"))


def test_views_keep_the_order_they_were_saved_in(specialist):
    for name in ("Kolmas", "Esimene", "Teine"):
        saved_views.save_view(user=specialist, name=name, params=QueryDict(f"q={name}"))

    assert [view.name for view in saved_views.personal_views(specialist, QueryDict())] == [
        "Kolmas",
        "Esimene",
        "Teine",
    ]


# ---------------------------------------------------------------------------
# The chip keeps its promise, through the one engine
# ---------------------------------------------------------------------------


def test_every_personal_chip_opens_exactly_the_rows_it_counted(client, specialist):
    """The count and the list come from one pipeline (search, status, filters)."""
    today = timezone.localdate()
    factories.MatterFactory(owner=specialist, title="Pakendiseaduse muutmine")
    factories.ArchiveMatterFactory(owner=specialist, title="Jäätmeseadus", is_open=False)
    factories.MatterFactory(owner=None, title="Vastutajata pakend")
    factories.MatterFactory(
        owner=specialist,
        title="Tähtajaga",
        response_deadline=today + timedelta(days=3),
    )
    definitions = {
        "Minu": "ulatus=minu",
        "Suletud": "olek=suletud",
        "Kõik olekud": "olek=",
        "Vastutajata": "vastutaja=puudub",
        "Otsing": "q=pakend&olek=koik",
        "Tähtajad": "too=tahtaeg-vahemik",
    }
    for name, query in definitions.items():
        saved_views.save_view(user=specialist, name=name, params=QueryDict(query))

    client.force_login(specialist)
    for view in saved_views.personal_views(specialist, QueryDict()):
        assert _total(client, view.query) == view.count, (
            f"«{view.name}» promises what it cannot show"
        )


def test_several_free_text_views_count_what_their_search_finds(
    client, specialist, django_assert_max_num_queries
):
    """Free text, with Estonian letters, a phrase and a filter beside it: each
    count is the rows its link opens, and each search adds its own two reads
    (the words' normalisation and the search index), nothing more."""
    factories.MatterFactory(owner=specialist, title="Pakendiseaduse muutmine")
    factories.MatterFactory(owner=specialist, title="Jäätmeseaduse täiendamine")
    factories.MatterFactory(owner=None, title="Õhusaaste vähendamise kava")
    factories.ArchiveMatterFactory(owner=specialist, title="Pakendiaktsiisi seadus", is_open=False)
    definitions = {
        "Pakend": "q=pakend",
        "Pakend kõik": "q=pakend&olek=koik",
        "Jäätmed": "q=jäätme",
        "Õhk vastutajata": "q=õhusaaste&vastutaja=puudub",
        "Fraas": "q=seaduse muutmine",
    }
    for name, query in definitions.items():
        saved_views.save_view(user=specialist, name=name, params=QueryDict(query))

    with django_assert_max_num_queries(2 + 2 * len(definitions)):
        views = saved_views.personal_views(specialist, QueryDict())
    client.force_login(specialist)
    counts = {view.name: view.count for view in views}
    for view in views:
        assert _total(client, view.query) == view.count, view.name
    assert counts["Pakend"] == 1 and counts["Pakend kõik"] == 2
    assert counts["Jäätmed"] == 1 and counts["Õhk vastutajata"] == 1


def test_a_complex_view_round_trips_through_its_address(client, specialist):
    """Repeated values, search, status, order, columns, page size and a work
    window: stored canonically, and the chip's address opens the same rows as
    the address it was saved from."""
    today = timezone.localdate()
    for number in range(6):
        factories.MatterFactory(
            owner=specialist if number % 2 else None,
            title=f"Näidisseadus {number} muutmine",
            response_deadline=today + timedelta(days=number),
        )
    address = (
        "q=näidisseadus&olek=koik&vastutaja=puudub&vastutaja=minu"
        "&jarjestus=title&kaupa=30&leht=2&too=tahtaeg-vahemik"
        f"&too_alates={today.isoformat()}&too_kuni={(today + timedelta(days=4)).isoformat()}"
    )
    client.force_login(specialist)
    expected = _total(client, address)

    response = client.post(SAVE, {"nimi": "Keeruline", "vaade": address, "next": "/teemad/"})

    view = SavedRegisterView.objects.get(user=specialist, name="Keeruline")
    assert "leht" not in view.query, "the page a reader was on is not part of a view"
    assert parse_qsl(view.query).count(("vastutaja", "minu")) == 1
    assert parse_qsl(view.query).count(("vastutaja", "puudub")) == 1
    assert response["Location"] == f"{REGISTER}?{view.query}"
    assert _total(client, view.query) == expected
    (chip,) = saved_views.personal_views(specialist, QueryDict(view.query))
    assert chip.active and chip.count == expected


@pytest.mark.parametrize(
    "stored",
    [
        "olek=avatud&vana_filter=1",
        "jarjestus=ei_ole_selline&olek=koik",
        "aasta=pole-aasta&olek=koik",
        "hetkeseis=kadunud_etapp&olek=koik",
        "too=ammu-kaotatud&olek=koik",
        "kaupa=999&olek=koik",
        "too_alates=31.02.2026&too=tahtaeg-vahemik",
    ],
)
def test_a_view_with_obsolete_parameters_still_opens(client, specialist, stored):
    """A name the register no longer reads, or a value it no longer knows: the
    strip draws, the chip counts and the link opens — never a server error."""
    factories.MatterFactory(owner=specialist, title="Näidisseadus")
    SavedRegisterView.objects.create(user=specialist, name="Vana", query=stored)
    client.force_login(specialist)

    page = client.get(REGISTER)
    assert page.status_code == 200
    (view,) = saved_views.personal_views(specialist, QueryDict())
    opened = client.get(f"{REGISTER}?{view.query}")
    assert opened.status_code == 200
    assert opened.context["total"] == view.count


def test_all_of_a_persons_views_are_counted_in_one_statement(
    specialist, django_assert_max_num_queries
):
    """Bounded by the cap, not by the register: one read of the rows and one
    aggregate, whatever the number of views (none here carries a search or a
    work-model filter, which would add one read each / one shared read)."""
    for number in range(saved_views.MAX_VIEWS):
        saved_views.save_view(
            user=specialist,
            name=f"Vaade {number}",
            params=QueryDict(f"aasta={2000 + number}&olek=koik"),
        )

    with django_assert_max_num_queries(3):
        views = saved_views.personal_views(specialist, QueryDict())
    assert len(views) == 20


def test_counting_goes_through_the_registers_own_selection():
    """No second filtering engine: the module reads `register_selection`."""
    import inspect

    source = inspect.getsource(saved_views.personal_views)
    assert "register_selection.selection_from" in source
    assert ".filtered(" in source and ".searched(" in source


def test_the_view_being_looked_at_is_marked(specialist):
    saved_views.save_view(user=specialist, name="Minu", params=QueryDict("ulatus=minu&olek=avatud"))

    (on,) = saved_views.personal_views(specialist, QueryDict("ulatus=minu&leht=2"))
    (off,) = saved_views.personal_views(specialist, QueryDict("olek=koik"))

    assert on.active, "an absent olek is avatud, and the page is not part of the view"
    assert not off.active


def test_a_parameter_the_register_no_longer_reads_is_dropped_when_opened(specialist):
    """A view saved before a filter was retired still opens, without it."""
    SavedRegisterView.objects.create(
        user=specialist, name="Vana", query="olek=avatud&vana_filter=1&ulatus=minu"
    )

    (view,) = saved_views.personal_views(specialist, QueryDict())

    assert view.query == "olek=avatud&ulatus=minu"
    assert view.dropped == ("vana_filter",)


# ---------------------------------------------------------------------------
# Private to the person
# ---------------------------------------------------------------------------


def test_another_persons_view_is_not_found(client, specialist, other_specialist):
    view = saved_views.save_view(user=specialist, name="Minu", params=QueryDict("ulatus=minu"))
    client.force_login(other_specialist)

    assert client.post(_rename(view), {"nimi": "Võõras"}).status_code == 404
    assert client.post(_remove(view)).status_code == 404
    view.refresh_from_db()
    assert view.name == "Minu"

    body = client.get(REGISTER).content.decode()
    assert "Minu vaated" not in body and _rename(view) not in body


def test_saving_takes_no_person_parameter(client, specialist, other_specialist):
    client.force_login(specialist)

    client.post(
        SAVE,
        {"nimi": "Minu", "vaade": "ulatus=minu", "user": str(other_specialist.pk)},
    )

    assert SavedRegisterView.objects.get().user == specialist


def test_the_routes_take_only_post(client, specialist):
    view = saved_views.save_view(user=specialist, name="Minu", params=QueryDict("ulatus=minu"))
    client.force_login(specialist)

    assert client.get(SAVE).status_code == 405
    assert client.get(_rename(view)).status_code == 405
    assert client.get(_remove(view)).status_code == 405


def test_a_reader_keeps_views_of_their_own(client, reader):
    """Presentation state, class C: a READER may keep views as a scratchpad."""
    client.force_login(reader)

    response = client.post(SAVE, {"nimi": "Lugemiseks", "vaade": "olek=koik"})

    assert response.status_code == 302
    assert SavedRegisterView.objects.filter(user=reader).count() == 1


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def test_saving_from_the_page_opens_the_saved_view(client, specialist):
    client.force_login(specialist)

    response = client.post(
        SAVE,
        {"nimi": "Suletud", "vaade": "olek=suletud&leht=3", "next": "/teemad/?olek=suletud"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/teemad/?olek=suletud"
    body = client.get(response.headers["Location"]).content.decode()
    assert "Vaade «Suletud» on salvestatud." in body
    assert 'class="uxchip uxchip--mine is-selected"' in body


def test_a_refused_save_returns_to_the_list_with_the_reason(client, specialist):
    saved_views.save_view(user=specialist, name="Sama", params=QueryDict("olek=koik"))
    client.force_login(specialist)

    response = client.post(
        SAVE, {"nimi": "sama", "vaade": "olek=avatud", "next": "/teemad/?olek=avatud"}
    )

    assert response.headers["Location"] == "/teemad/?olek=avatud"
    assert "on sul juba olemas" in client.get(response.headers["Location"]).content.decode()


@pytest.mark.parametrize("target", ["https://elsewhere.example/teemad/", "//x", "/haldus/"])
def test_the_way_back_is_only_ever_teemad(client, specialist, target):
    view = saved_views.save_view(user=specialist, name="Minu", params=QueryDict("ulatus=minu"))
    client.force_login(specialist)

    response = client.post(_rename(view), {"nimi": "Uus", "next": target})

    assert response.headers["Location"] == REGISTER


def test_rename_and_remove(client, specialist):
    view = saved_views.save_view(user=specialist, name="Vana nimi", params=QueryDict("q=x"))
    client.force_login(specialist)

    client.post(_rename(view), {"nimi": "Uus nimi", "next": "/teemad/?q=x"})
    view.refresh_from_db()
    assert view.name == "Uus nimi"
    assert view.query == "q=x", "renaming leaves the filters alone"

    client.post(_remove(view), {"next": "/teemad/"})
    assert not SavedRegisterView.objects.exists()


def test_the_strip_stays_compact_with_many_views(client, specialist):
    for number in range(9):
        saved_views.save_view(
            user=specialist, name=f"Vaade {number}", params=QueryDict(f"aasta={2010 + number}")
        )
    client.force_login(specialist)

    body = client.get(REGISTER).content.decode()
    strip = body.split('class="uxviews"')[1].split('class="filters"')[0]

    assert strip.count('class="uxchip uxchip--mine') == saved_views.VISIBLE_CHIPS
    assert '<span class="uxviews__more--wide"> · +3</span>' in strip
    assert strip.count('class="uxviews__item"') == 9, "every view is in the list"


def test_twenty_views_draw_six_chips_three_of_them_on_a_narrow_screen(client, specialist):
    """Twenty views: six chips (three of them kept at ≤ 720 px, the rest marked
    for the narrow rule in ux.css), «+14» / «+17» after «Minu vaated», all
    twenty in its list — and the twenty-first refused in words."""
    for number in range(saved_views.MAX_VIEWS):
        saved_views.save_view(
            user=specialist, name=f"Vaade {number}", params=QueryDict(f"aasta={2000 + number}")
        )
    client.force_login(specialist)

    body = client.get(REGISTER).content.decode()
    strip = body.split('class="uxviews"')[1].split('class="filters"')[0]

    assert strip.count('class="uxchip uxchip--mine') == saved_views.VISIBLE_CHIPS
    assert strip.count("uxchip--minewide") == saved_views.VISIBLE_CHIPS - saved_views.NARROW_CHIPS
    assert '<span class="uxviews__more--wide"> · +14</span>' in strip
    assert '<span class="uxviews__more--narrow"> · +17</span>' in strip
    assert strip.count('class="uxviews__item"') == saved_views.MAX_VIEWS

    response = client.post(
        SAVE, {"nimi": "Kahekümne esimene", "vaade": "olek=koik", "next": "/teemad/"}, follow=True
    )
    assert response.status_code == 200
    assert "Sul on juba 20 vaadet" in response.content.decode()
    assert SavedRegisterView.objects.filter(user=specialist).count() == saved_views.MAX_VIEWS


def test_a_few_views_need_no_more_than_their_chips(client, specialist):
    """Three views fit a narrow screen too: no «+N» at either width."""
    for number in range(saved_views.NARROW_CHIPS):
        saved_views.save_view(
            user=specialist, name=f"Vaade {number}", params=QueryDict(f"aasta={2000 + number}")
        )
    client.force_login(specialist)

    strip = client.get(REGISTER).content.decode().split('class="uxviews"')[1]

    assert "uxchip--minewide" not in strip.split('class="filters"')[0]
    assert "uxviews__more--" not in strip.split('class="filters"')[0]


def test_at_the_limit_the_save_form_says_why_instead(client, specialist):
    for number in range(saved_views.MAX_VIEWS):
        saved_views.save_view(
            user=specialist, name=f"Vaade {number}", params=QueryDict(f"aasta={2000 + number}")
        )
    client.force_login(specialist)

    body = client.get(f"{REGISTER}?olek=koik").content.decode()

    assert "Sul on juba 20 vaadet" in body
    assert 'id="teemad-vaate-nimi"' not in body


def test_the_strip_is_not_counted_on_a_live_search_keystroke(client, specialist):
    """Like the four chips: the fragment the search box swaps carries no strip."""
    saved_views.save_view(user=specialist, name="Minu", params=QueryDict("ulatus=minu"))
    client.force_login(specialist)

    fragment = client.get(REGISTER, {"q": "x"}, HTTP_HX_REQUEST="true").content.decode()

    assert "uxchip--mine" not in fragment


def test_a_saved_views_link_is_a_plain_register_address(client, specialist):
    """The address is still the view: no id of the stored row in the link."""
    view = saved_views.save_view(user=specialist, name="Minu", params=QueryDict("ulatus=minu"))
    client.force_login(specialist)

    body = client.get(REGISTER).content.decode()

    assert 'href="?ulatus=minu"' in body
    assert f"?{view.pk}" not in body


def test_parse_of_a_stored_query_matches_the_definition(specialist):
    view = saved_views.save_view(
        user=specialist, name="Kaks", params=QueryDict("vastutaja=a&vastutaja=b")
    )
    assert parse_qsl(view.query) == [("vastutaja", "a"), ("vastutaja", "b")]
