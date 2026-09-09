"""`/uuendused/` — the day-grouped release notes, and the footer link to them.

Three things are worth holding here and each one has already been got wrong
somewhere else in this repository:

* **the grouping**, because "one accordion per day" is the whole decision. A
  renderer that emits one per change, or one per release, produces a page that
  still looks plausible;
* **the source**, because a release-note file that half-parses would leave a
  page showing an incomplete history with nothing to say so;
* **the escaping**, because these strings are prose somebody typed, and a
  release note is a poor place to discover that a page trusts its content.

Structure and invariants, not the wording. Locking a paragraph would make an
ordinary copy edit a failing test, which is how a suite teaches people to stop
editing copy.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
from django.test import Client
from django.urls import reverse

from app.core.release_notes import (
    ReleaseDay,
    ReleaseNotesError,
    load_release_notes,
    parse_release_notes,
    release_notes_path,
)
from tests.gate import apply_shared_gate

URL = "/uuendused/"

PASSWORD = "seda-parooli-ei-ole-kusagil-mujal"  # noqa: S105

#: A whole, valid file, so that each parser test can vary exactly one thing.
GOOD = """
[[day]]
date = 2026-09-09
changes = ["Teine asi", "Kolmas asi"]

[[day]]
date = 2026-09-01
changes = ["Esimene asi"]
"""


def one_day(body: str) -> str:
    return f"[[day]]\n{body}\n"


# ---------------------------------------------------------------------------
# The route and the page
# ---------------------------------------------------------------------------


def test_the_route_is_uuendused() -> None:
    assert reverse("core:release_notes") == URL


def test_the_page_is_titled_uuendused(client: Client) -> None:
    body = client.get(URL).content.decode()
    assert "<title>Uuendused" in body
    assert re.search(r"<h1[^>]*>\s*Uuendused\s*</h1>", body)


def test_the_page_renders_every_committed_day(client: Client) -> None:
    body = client.get(URL).content.decode()
    for day in load_release_notes():
        assert day.heading in body, day.heading


def test_the_newest_day_comes_first(client: Client) -> None:
    """Not merely "sorted": the first heading on the page is the newest one.

    Asserted against the rendering rather than against the loader, because the
    template is free to reverse what it was handed and the loader would not
    notice.
    """
    body = client.get(URL).content.decode()
    days = load_release_notes()
    positions = [body.index(day.heading) for day in days]
    assert positions == sorted(positions)
    assert days[0].date == max(day.date for day in days)


def test_the_newest_day_is_open_and_the_others_are_shut(client: Client) -> None:
    body = client.get(URL).content.decode()
    opened = re.findall(r"<details class=\"accordion relnotes__day\"( open)?>", body)
    assert opened, "no day accordions were rendered"
    assert opened[0] == " open"
    assert set(opened[1:]) <= {""}


def test_one_accordion_per_day_and_no_more(client: Client) -> None:
    """The central decision: several changes on one day live in ONE disclosure.

    Counted against the file rather than against a number written here, so
    adding a day cannot make this pass for the wrong reason.
    """
    body = client.get(URL).content.decode()
    days = load_release_notes()
    assert body.count('class="accordion relnotes__day"') == len(days)
    assert sum(day.count for day in days) > len(days), (
        "every committed day carries exactly one change, so this test could not "
        "tell one-accordion-per-day from one-accordion-per-change"
    )


def test_a_day_with_several_changes_holds_them_all_inside_its_own_accordion() -> None:
    days = parse_release_notes(GOOD)
    rendered = _render(days)
    block = _accordion_holding(rendered, "9. september 2026")
    assert "Teine asi" in block
    assert "Kolmas asi" in block
    assert "Esimene asi" not in block


def test_no_date_gets_a_second_accordion(client: Client) -> None:
    body = client.get(URL).content.decode()
    headings = re.findall(r"<h2 class=\"accordion__title\">([^<]+)</h2>", body)
    assert headings
    assert len(headings) == len(set(headings))


def test_the_lead_says_the_page_is_grouped_by_day(client: Client) -> None:
    """The one sentence a reader needs to interpret the accordions."""
    assert "päevade kaupa" in client.get(URL).content.decode()


# ---------------------------------------------------------------------------
# The count beside the date
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "1 uuendus"), (2, "2 uuendust"), (7, "7 uuendust"), (11, "11 uuendust")],
)
def test_the_count_takes_the_partitive_after_every_number_but_one(
    count: int, expected: str
) -> None:
    day = ReleaseDay(date=date(2026, 9, 9), changes=tuple(f"muudatus {n}" for n in range(count)))
    assert day.count_label == expected


def test_the_count_beside_a_date_is_the_number_of_changes_under_it(client: Client) -> None:
    body = client.get(URL).content.decode()
    for day in load_release_notes():
        block = _accordion_holding(body, day.heading)
        assert day.count_label in block
        assert block.count("<li>") == day.count


def test_the_day_is_written_out_as_text(client: Client) -> None:
    """A date conveyed as text, not by position, colour or an icon.

    Also the reason it is not a `<time>`: the visual suite masks every `<time>`
    on a captured page as clock-derived, and these dates are static content the
    baseline has to keep (`templates/core/release_notes.html`).
    """
    assert ReleaseDay(date=date(2026, 9, 9), changes=("x",)).heading == "9. september 2026"
    body = client.get(URL).content.decode()
    assert "<time" not in _accordion_holding(body, load_release_notes()[0].heading)


# ---------------------------------------------------------------------------
# Nothing is trusted, nothing is queried
# ---------------------------------------------------------------------------


def test_release_note_text_is_escaped(client: Client) -> None:
    """A typo in a release note must not be able to become markup."""
    days = parse_release_notes(
        one_day('date = 2026-09-09\nchanges = ["Kui <script>alert(1)</script> & muu"]')
    )
    rendered = _render(days)
    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; muu" in rendered


@pytest.mark.django_db
def test_the_page_needs_no_database_at_all(client: Client, django_assert_num_queries) -> None:
    """Zero queries — including the session, the persona and the navigation.

    The database is *available* here only because counting queries needs a
    connection to count on. Every other test in this module runs without the
    mark at all, which is the same claim from the other side: a page that
    reached the database for its content would fail to render there and would
    merely be slower here.

    The release notes are a file in the image. A page that read them from a
    table would pass every assertion above and fail only this one.
    """
    with django_assert_num_queries(0):
        assert client.get(URL).status_code == 200


def test_nothing_reaches_the_network(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """No GitHub call at request time, and no call of any other kind."""
    import socket

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the release-notes page opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert client.get(URL).status_code == 200


def test_the_source_is_parsed_once_per_process() -> None:
    load_release_notes.cache_clear()
    first = load_release_notes()
    assert load_release_notes() is first


# ---------------------------------------------------------------------------
# The committed source
# ---------------------------------------------------------------------------


def test_the_committed_source_parses() -> None:
    assert load_release_notes()


def test_the_committed_source_has_one_entry_per_day() -> None:
    days = load_release_notes()
    assert len({day.date for day in days}) == len(days)


def test_every_committed_day_carries_at_least_one_change() -> None:
    assert all(day.changes for day in load_release_notes())


def test_no_committed_change_names_a_pull_request_or_a_revision() -> None:
    """Section 8 of the brief, as a check rather than as a habit.

    `PR #143`, `#143`, `abc1234` and a branch path are all things a reader of
    this page has no use for. The provenance lives in the file's own metadata,
    which is never rendered.
    """
    forbidden = re.compile(r"\bPR\b|#\d+|\b[0-9a-f]{7,40}\b|\b(?:feature|fix|ux|hardening)/")
    for day in load_release_notes():
        for change in day.changes:
            assert not forbidden.search(change), f"{day.date}: {change!r}"


def test_the_september_ninth_group_is_still_there() -> None:
    """The batch this page launched with, held against an accidental deletion.

    Subjects, not sentences: the wording is meant to be edited. What may not
    quietly disappear is that the day is described at all, and that each of the
    outcomes it shipped is still named somewhere in it.
    """
    days = {day.date: day for day in load_release_notes()}
    assert date(2026, 9, 9) in days, "the 9 September group is gone"
    text = " ".join(days[date(2026, 9, 9)].changes).lower()
    for subject in (
        "uue teema",  # the assisted intake, on the form itself
        "failid alles",  # a refused save keeps the files
        "saatja",  # sender selection and naming a new one
        "jõustumise",  # inline on the Matter
        "töövõidu",  # inline on the Matter
        "kokkuvõte",  # the folded run summary
        "hüüumärk",  # the unread Uus asi mark
        "09.26",  # the compact month date
    ):
        assert subject in text, subject


def test_the_committed_source_lives_where_the_maintainer_note_says() -> None:
    path = release_notes_path()
    assert path.is_file()
    assert (path.parent / "README.md").is_file()


# ---------------------------------------------------------------------------
# What the parser refuses
# ---------------------------------------------------------------------------


def test_a_duplicate_date_is_refused() -> None:
    text = one_day('date = 2026-09-09\nchanges = ["A"]') + one_day(
        'date = 2026-09-09\nchanges = ["B"]'
    )
    with pytest.raises(ReleaseNotesError, match="2026-09-09"):
        parse_release_notes(text)


def test_a_malformed_date_is_refused() -> None:
    with pytest.raises(ReleaseNotesError, match="2026-09-09"):
        parse_release_notes(one_day('date = "eile"\nchanges = ["A"]'))


def test_a_date_that_no_calendar_has_is_refused() -> None:
    with pytest.raises(ReleaseNotesError):
        parse_release_notes(one_day('date = "2026-13-45"\nchanges = ["A"]'))


def test_an_empty_change_is_refused() -> None:
    with pytest.raises(ReleaseNotesError, match="change #2"):
        parse_release_notes(one_day('date = 2026-09-09\nchanges = ["A", "   "]'))


def test_a_day_with_no_changes_is_refused() -> None:
    with pytest.raises(ReleaseNotesError, match="at least one change"):
        parse_release_notes(one_day("date = 2026-09-09\nchanges = []"))


def test_a_day_with_no_date_is_refused() -> None:
    with pytest.raises(ReleaseNotesError, match="date"):
        parse_release_notes(one_day('changes = ["A"]'))


def test_an_unknown_basis_is_refused() -> None:
    with pytest.raises(ReleaseNotesError, match="basis"):
        parse_release_notes(one_day('date = 2026-09-09\nbasis = "vibes"\nchanges = ["A"]'))


def test_a_file_that_is_not_toml_is_refused() -> None:
    with pytest.raises(ReleaseNotesError):
        parse_release_notes("[[day]\ndate = 2026-09-09")


def test_a_file_with_no_days_at_all_is_refused() -> None:
    with pytest.raises(ReleaseNotesError, match="'day'"):
        parse_release_notes("# nothing here\n")


def test_a_file_with_an_empty_day_list_is_refused() -> None:
    """An empty history is a broken file, not a quiet fortnight."""
    with pytest.raises(ReleaseNotesError, match="no days"):
        parse_release_notes("day = []\n")


def test_a_quoted_iso_date_is_accepted() -> None:
    (day,) = parse_release_notes(one_day('date = "2026-09-09"\nchanges = ["A"]'))
    assert day.date == date(2026, 9, 9)


def test_the_renderer_sorts_rather_than_trusting_the_file_order() -> None:
    text = one_day('date = 2026-09-01\nchanges = ["A"]') + one_day(
        'date = 2026-09-09\nchanges = ["B"]'
    )
    assert [day.date for day in parse_release_notes(text)] == [
        date(2026, 9, 9),
        date(2026, 9, 1),
    ]


# ---------------------------------------------------------------------------
# The footer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_footer_offers_uuendused(client: Client) -> None:
    footer = _footer(client.get("/").content.decode())
    assert f'href="{URL}"' in footer
    assert "Uuendused" in footer


@pytest.mark.django_db
def test_the_footer_still_says_which_build_is_running(client: Client, settings) -> None:
    """The link is added beside the stamp; it replaces nothing.

    `tests/test_build_stamp.py` owns the stamp's own semantics. This is the
    narrower claim that adding a link did not push any of it off the page.
    """
    settings.APPLICATION_REVISION = "ceb324c"
    settings.APPLICATION_BUILT_AT = "2026-08-19T11:52:22Z"
    settings.APPLICATION_STAGE = "Stage 4"
    settings.APPLICATION_ENVIRONMENT = "production"

    footer = _footer(client.get("/").content.decode())
    assert "Stage 4" in footer
    assert "production" in footer
    assert "versioon ceb324c" in footer
    assert "ehitatud 19.8.2026 14:52" in footer


@pytest.mark.django_db
def test_the_footer_omits_the_build_time_when_there_is_none_and_still_links(
    client: Client, settings
) -> None:
    """The link sits before the conditional half, so it survives its absence."""
    settings.APPLICATION_BUILT_AT = ""
    footer = _footer(client.get("/").content.decode())
    assert "ehitatud" not in footer
    assert f'href="{URL}"' in footer


@pytest.mark.django_db
def test_the_revision_itself_is_not_a_link(client: Client, settings) -> None:
    settings.APPLICATION_REVISION = "ceb324c"
    footer = _footer(client.get("/").content.decode())
    assert re.search(r"versioon ceb324c\b(?![^<]*</a>)", footer)


@pytest.mark.django_db
def test_the_footer_carries_exactly_one_link(client: Client) -> None:
    """Quiet band, not a navigation bar."""
    assert _footer(client.get("/").content.decode()).count("<a ") == 1


def test_the_release_notes_page_carries_the_footer_too(client: Client) -> None:
    assert "app__footer" in client.get(URL).content.decode()


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@pytest.fixture
def gate_mode(settings):
    return apply_shared_gate(settings, PASSWORD)


@pytest.mark.django_db
def test_the_shared_gate_still_stands_in_front_of_the_release_notes(client, gate_mode) -> None:
    """No public bypass. `/uuendused/` is behind the same door as everything.

    The exempt list is `/healthz` and `/static/` and this change did not touch
    it, which is exactly the claim worth holding: a page that is nice to read
    before signing in is how an exemption gets added and then forgotten.
    """
    response = client.get(URL)
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:shared_gate")


@pytest.mark.django_db
def test_a_reader_past_the_gate_needs_no_persona_to_read_them(client, gate_mode) -> None:
    """The point of not decorating the view with `login_required`.

    A persona answers *whose work are you looking at*, and there is no work on
    this page. Somebody who has typed the department password and not yet said
    who they are can read what changed — which is the moment they are most
    likely to want to.
    """
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302

    response = client.get(URL)
    assert response.status_code == 200
    assert not response.wsgi_request.user.is_authenticated
    assert load_release_notes()[0].heading in response.content.decode()


@pytest.mark.django_db
def test_a_named_persona_reads_the_same_page(client, gate_mode) -> None:
    from tests import factories

    client.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    marko = factories.UserFactory(display_name="Marko Näidisjurist")
    client.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})

    response = client.get(URL)
    assert response.status_code == 200
    assert response.wsgi_request.user.pk == marko.pk


@pytest.mark.django_db
def test_the_footer_link_is_offered_at_the_gate_itself_only_as_far_as_it_works(
    client, gate_mode
) -> None:
    """Whatever the gate page renders, following the link cannot skip the gate.

    Stated as a property of the route rather than of the template: the base
    template is shared, so the honest guard is that the destination refuses.
    """
    gate = client.get(reverse("accounts:shared_gate")).content.decode()
    if f'href="{URL}"' in gate:
        assert client.get(URL).status_code == 302


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render(days: tuple[ReleaseDay, ...]) -> str:
    from django.template.loader import render_to_string

    return render_to_string("core/release_notes.html", {"release_days": days})


def _accordion_holding(body: str, heading: str) -> str:
    """One `<details>` block, from the day's own heading to the next day's."""
    start = body.rindex("<details", 0, body.index(heading))
    end = body.find("<details", start + 1)
    return body[start:] if end == -1 else body[start:end]


def _footer(body: str) -> str:
    start = body.index('<footer class="app__footer">')
    return body[start : body.index("</footer>", start)]
