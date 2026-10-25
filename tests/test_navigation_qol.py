"""Finding your way back, to one row, and to what you just saved.

Three small navigation aids on the Teema page, each on existing routes:

* **«Tagasi tulemustesse»** — offered only when the page was opened from a
  narrowed Teemad list, and only to that list: the return address is rebuilt
  from the browser's `Referer` and refused for every other host, scheme and
  path (`app.matters.return_link`).
* **A link to one `Tegevused` row** — `?kirje=<row key>` draws enough rows to
  include the row, bounded; an unknown key, a key from another Teema and a key
  past the bound all leave the page exactly as it is without one
  (`app.matters.timeline_links`).
* **The row a save wrote** — marked on the answer to a successful save and on
  nothing else (`data-salvestatud`).

The browser half is `e2e/test_navigation_qol.py`.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.core import authorization
from app.core.authorization import Scope
from app.core.enums import Visibility
from app.matters import timeline_links
from app.matters import views as matter_views
from app.matters.episode_timeline import GROUPED_WINDOW
from app.matters.models import Entry
from app.matters.removal import remove_matter_record
from app.matters.return_link import results_return_url
from app.matters.services import add_entry, change_stage, entry_revision_token
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db

LIST = "/teemad/"
RETURN_LINK = "Tagasi tulemustesse"


def _detail(matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


def _normalized(html: str) -> str:
    """The page with its per-request tokens taken out: CSRF, and each form's save-once token."""
    html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "csrf", html)
    html = re.sub(r'name="salvestus" value="[^"]+"', "salvestus", html)
    html = re.sub(r'"X-CSRFToken": "[^"]+"', "csrf", html)
    return re.sub(r'content="[A-Za-z0-9]{64}"', "csrf", html)


# -- «Tagasi tulemustesse» --------------------------------------------------------


def _from(referer: str | None, *, host: str = "testserver"):
    headers = {"HTTP_HOST": host}
    if referer is not None:
        headers["HTTP_REFERER"] = referer
    return RequestFactory().get("/teemad/x/", **headers)


def test_a_narrowed_list_on_this_host_is_offered_back_rebuilt():
    request = _from("http://testserver/teemad/?q=eeln%C3%B5u&olek=avatud&leht=2")

    assert results_return_url(request) == "/teemad/?q=eeln%C3%B5u&olek=avatud&leht=2"


def test_a_blank_choice_in_the_list_address_is_kept():
    """`olek=` (every state) is a choice; dropping it would return to another list."""
    request = _from("http://testserver/teemad/?olek=&q=kliima")

    assert results_return_url(request) == "/teemad/?olek=&q=kliima"


def test_the_return_address_is_re_encoded_not_echoed():
    request = _from('http://testserver/teemad/?q="><script>alert(1)</script>')

    url = results_return_url(request)

    assert url.startswith("/teemad/?q=")
    assert "<" not in url and '"' not in url


@pytest.mark.parametrize(
    "referer",
    [
        None,
        "",
        # Another host, however the address is spelled.
        "https://evil.example/teemad/?q=x",
        "//evil.example/teemad/?q=x",
        "http://testserver.evil.example/teemad/?q=x",
        "http://evil.example@testserver.example/teemad/?q=x",
        # Not a web address at all.
        "javascript:alert(1)//testserver/teemad/?q=x",
        "data:text/html,<a href=/teemad/?q=x>",
        # Relative: a browser never sends one.
        "/teemad/?q=x",
        # This host, but not the register.
        "http://testserver/minu-asjad/?q=x",
        "http://testserver/teemad/uus/?q=x",
        "http://testserver/teemad/01a17406-6a72-747e-aae8-b6827e767618/?q=x",
        "http://testserver/teemadx/?q=x",
        # The bare register: the top bar already goes there.
        "http://testserver/teemad/",
        "http://testserver/teemad/?",
    ],
)
def test_anything_but_a_narrowed_list_on_this_host_offers_no_way_back(referer):
    assert results_return_url(_from(referer)) == ""


def test_an_over_long_referer_is_not_honoured():
    referer = "http://testserver/teemad/?q=" + "a" * 3000

    assert results_return_url(_from(referer)) == ""


def test_the_teema_opened_from_a_narrowed_list_offers_the_link(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    html = signed_in.get(
        _detail(matter), HTTP_REFERER="http://testserver/teemad/?q=kliima&leht=2"
    ).content.decode()

    assert RETURN_LINK in html
    assert 'href="/teemad/?q=kliima&amp;leht=2"' in html


def test_a_deep_link_to_a_teema_shows_no_way_back(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    plain = signed_in.get(_detail(matter)).content.decode()
    elsewhere = signed_in.get(
        _detail(matter), HTTP_REFERER="https://evil.example/teemad/?q=kliima"
    ).content.decode()

    assert RETURN_LINK not in plain
    assert RETURN_LINK not in elsewhere


# -- a link to one row ------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "",
        "x",
        "1 OR 1=1",
        "../",
        "onenote:",
        "onenote:<b>",
        "01a17406-6a72-747e-aae8-b6827e767618x",
    ],
)
def test_a_value_that_is_no_row_key_is_ignored_before_any_query(value):
    request = RequestFactory().get("/teemad/x/", {"kirje": value})

    assert timeline_links.requested_row(request) == ""


def test_a_row_key_is_read_only_on_a_page_view():
    key = str(uuid.uuid4())

    assert timeline_links.requested_row(RequestFactory().get("/", {"kirje": key})) == key
    assert timeline_links.requested_row(RequestFactory().post("/?kirje=" + key)) == ""
    assert timeline_links.requested_row(
        RequestFactory().get("/", {"kirje": "onenote:day:2026-01-31"})
    )


@pytest.fixture
def long_flat(specialist):
    """A Teema with no `Hetkeseis` period — the flat, paged chronology — and 40 notes."""
    matter = factories.MatterFactory(owner=specialist, stage=None)
    for number in range(40):
        add_entry(matter=matter, author=specialist, body=f"Lame märge {number:03d}")
    return matter


def _entry(matter, body: str) -> Entry:
    return Entry.objects.get(matter=matter, body__contains=body)


def test_every_open_row_offers_its_own_link(signed_in, long_flat):
    newest = _entry(long_flat, "Lame märge 039")

    html = signed_in.get(_detail(long_flat)).content.decode()

    assert f'data-copy-href="{_detail(long_flat)}?kirje={newest.pk}#kaik-{newest.pk}-rida"' in html
    assert "Kopeeri link" in html


def test_a_link_to_a_row_behind_the_first_page_draws_it(signed_in, long_flat):
    oldest = _entry(long_flat, "Lame märge 000")
    plain = signed_in.get(_detail(long_flat)).content.decode()

    linked = signed_in.get(_detail(long_flat), {"kirje": str(oldest.pk)}).content.decode()

    assert "Lame märge 000" not in plain
    assert "Lame märge 000" in linked
    assert f'id="kaik-{oldest.pk}-rida"' in linked
    # «Näita varasemaid» continues after the rows drawn, not after the first 30.
    assert "nihe=60" in linked or "Näita varasemaid" not in linked


def test_the_row_stays_reachable_after_unrelated_new_activity(signed_in, specialist, long_flat):
    oldest = _entry(long_flat, "Lame märge 000")
    for number in range(30):
        add_entry(matter=long_flat, author=specialist, body=f"Hilisem märge {number:03d}")

    linked = signed_in.get(_detail(long_flat), {"kirje": str(oldest.pk)}).content.decode()

    assert "Lame märge 000" in linked


@pytest.mark.parametrize("kind", ["unknown", "another_teema", "removed_shape"])
def test_a_key_this_teema_does_not_show_reads_as_no_key_at_all(
    signed_in, specialist, long_flat, kind
):
    if kind == "unknown":
        key = str(uuid.uuid4())
    elif kind == "another_teema":
        elsewhere = factories.MatterFactory(owner=specialist, stage=None)
        key = str(add_entry(matter=elsewhere, author=specialist, body="Teise teema märge").pk)
    else:
        key = "onenote:day:2001-01-01"

    plain = signed_in.get(_detail(long_flat)).content.decode()
    linked = signed_in.get(_detail(long_flat), {"kirje": key}).content.decode()

    assert _normalized(linked) == _normalized(plain)
    assert "Teise teema märge" not in linked


def test_a_removed_entry_reads_as_no_key_at_all(signed_in, specialist, long_flat):
    """Taken off the file, on a Teema with more rows than one page: the link
    somebody sent before the removal opens the page as if it named nothing."""
    removed = _entry(long_flat, "Lame märge 000")
    remove_matter_record(
        matter_id=long_flat.pk, kind_key="sissekanne", record_id=removed.pk, actor=specialist
    )

    plain = signed_in.get(_detail(long_flat)).content.decode()
    linked = signed_in.get(_detail(long_flat), {"kirje": str(removed.pk)}).content.decode()

    assert _normalized(linked) == _normalized(plain)
    assert "Lame märge 000" not in linked


#: The modules that resolve a reader's scope while the Teema page renders.
_SCOPED_MODULES = (
    "app.matters.models",
    "app.audit.visibility",
    "app.workflow.models",
    "app.submissions.models",
    "app.intelligence.models",
    "app.documents.links",
    "app.matters.activity",
)


def test_an_entry_hidden_from_this_reader_reads_as_no_key_at_all(
    client, specialist, other_specialist, long_flat, monkeypatch
):
    """RESTRICTED below a NORMAL Teema, read through the participation-narrowed
    scope (no request builds it since docs/adr/0181, but the builders still
    implement it — the same hand-built scope
    `tests/test_child_projection_visibility.py` reads through). The hidden row
    is not in this reader's chronology, so its key finds nothing and changes
    nothing."""
    hidden = add_entry(matter=long_flat, author=specialist, body="Piiratud sisu märge")
    Entry.objects.filter(pk=hidden.pk).update(visibility_override=Visibility.RESTRICTED)
    real = authorization.scope_for_user

    def narrowed(user):
        if getattr(user, "pk", None) == other_specialist.pk:
            return Scope(
                user=user,
                is_authenticated=True,
                sees_restricted_by_role=False,
                break_glass_grant_id=None,
            )
        return real(user)

    for module in _SCOPED_MODULES:
        monkeypatch.setattr(f"{module}.scope_for_user", narrowed)
    client.force_login(other_specialist)

    plain = client.get(_detail(long_flat)).content.decode()
    linked = client.get(_detail(long_flat), {"kirje": str(hidden.pk)}).content.decode()

    assert "Lame märge 039" in plain  # the reader does read the Teema
    assert "Piiratud sisu märge" not in plain
    assert _normalized(linked) == _normalized(plain)


def test_looking_for_a_row_is_bounded(signed_in, specialist, monkeypatch):
    monkeypatch.setattr(matter_views, "TIMELINE_PAGE_SIZE", 5)
    monkeypatch.setattr(timeline_links, "LOCATE_LIMIT", 10)
    matter = factories.MatterFactory(owner=specialist, stage=None)
    for number in range(15):
        add_entry(matter=matter, author=specialist, body=f"Piiratud märge {number:03d}")
    within = _entry(matter, "Piiratud märge 007")  # index 7: inside the bound
    beyond = _entry(matter, "Piiratud märge 000")  # index 14: past it

    reached = signed_in.get(_detail(matter), {"kirje": str(within.pk)}).content.decode()
    missed = signed_in.get(_detail(matter), {"kirje": str(beyond.pk)}).content.decode()

    assert "Piiratud märge 007" in reached
    assert "nihe=10" in reached
    assert "Piiratud märge 000" not in missed
    assert "nihe=5" in missed


def test_an_unknown_key_costs_one_bounded_read_and_no_more(signed_in, long_flat):
    page = _detail(long_flat)
    signed_in.get(page)  # warm whatever is cached per process

    with CaptureQueriesContext(connection) as plain:
        signed_in.get(page)
    with CaptureQueriesContext(connection) as unknown:
        signed_in.get(page, {"kirje": str(uuid.uuid4())})

    # One `matter_timeline` read, whatever the history's length: its own
    # handful of statements, never a statement per row.
    assert len(unknown) - len(plain) <= 25


def test_a_grouped_chronology_opens_the_period_holding_the_linked_row(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, stage=None)
    change_stage(matter=matter, stage=StageVocabulary.objects.get(key="idea"), actor=specialist)
    for number in range(GROUPED_WINDOW):
        add_entry(matter=matter, author=specialist, body=f"Idee märge {number:03d}")
    change_stage(
        matter=matter, stage=StageVocabulary.objects.get(key="consultation"), actor=specialist
    )
    for number in range(GROUPED_WINDOW // 2):
        add_entry(matter=matter, author=specialist, body=f"Kooskõlastuse märge {number:03d}")
    oldest = _entry(matter, "Idee märge 000")

    plain = signed_in.get(_detail(matter)).content.decode()
    linked = signed_in.get(_detail(matter), {"kirje": str(oldest.pk)}).content.decode()

    assert "Idee märge 000" not in plain
    assert "Idee märge 000" in linked
    period = linked[: linked.index(f'id="kaik-{oldest.pk}-rida"')]
    period = period[period.rindex('<details class="kaikstage') :]
    assert period.split(">", 1)[0].rstrip().endswith("open")


# -- the row a save wrote -----------------------------------------------------------


def _post_other(client, matter, description: str):
    return client.post(
        reverse("matters:add_other", kwargs={"pk": matter.pk}),
        {"description": description, "url": "", "occurred_on": "1.10.2026"},
        headers={"HX-Request": "true"},
    )


def test_a_successful_save_marks_the_row_it_wrote_and_no_other(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, stage=None)
    earlier = add_entry(matter=matter, author=specialist, body="Varasem märge")

    response = _post_other(signed_in, matter, "Uus taustamaterjal")

    assert response.status_code == 200
    saved = Entry.objects.get(matter=matter, body__contains="Uus taustamaterjal")
    html = response.content.decode()
    marked = re.findall(r'data-kaik-rida="([^"]+)" data-salvestatud', html)
    assert marked == [str(saved.pk)]
    assert str(earlier.pk) not in marked


def test_a_refused_save_marks_nothing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, stage=None)
    add_entry(matter=matter, author=specialist, body="Varasem märge")

    response = _post_other(signed_in, matter, "")

    assert response.status_code in {400, 422}
    assert "data-salvestatud" not in response.content.decode()


def test_a_page_view_marks_nothing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, stage=None)
    _post_other(signed_in, matter, "Uus taustamaterjal")

    html = signed_in.get(_detail(matter)).content.decode()

    assert "data-salvestatud" not in html


# -- the row a correction in place saved --------------------------------------------


def _edit_entry(matter, entry) -> str:
    return reverse("matters:edit_entry", kwargs={"pk": matter.pk, "entry_id": entry.pk})


def test_a_correction_in_place_marks_its_row_and_nothing_else_does(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, stage=None)
    entry = add_entry(matter=matter, author=specialist, body="<p>Esialgne tekst.</p>")
    url = _edit_entry(matter, entry)

    opened = signed_in.get(url, headers={"HX-Request": "true"})
    refused = signed_in.post(
        url, {"body": "", "revision": entry_revision_token(entry)}, headers={"HX-Request": "true"}
    )
    saved = signed_in.post(
        url,
        {"body": "<p>Parandatud tekst.</p>", "revision": entry_revision_token(entry)},
        headers={"HX-Request": "true"},
    )

    assert opened.status_code == 200
    assert "data-salvestatud" not in opened.content.decode()
    assert refused.status_code >= 400
    assert "data-salvestatud" not in refused.content.decode()
    assert saved.status_code == 200
    html = saved.content.decode()
    assert f'id="sissekanne-{entry.pk}-sisu" data-draft-host data-salvestatud' in html


@pytest.mark.parametrize(
    "partial",
    ["engagement_row", "external_position_row", "development_row", "submission_row", "entry_body"],
)
def test_every_in_place_correction_carries_the_saved_mark(partial):
    """The five elements `Muuda` swaps in place each carry the mark their renderer
    sets (`_row_saved`); one of them is exercised end to end above."""
    text = (
        Path(settings.BASE_DIR) / "templates" / "matters" / "partials" / f"{partial}.html"
    ).read_text(encoding="utf-8")

    assert "data-draft-host{% if row_saved %} data-salvestatud{% endif %}" in text
