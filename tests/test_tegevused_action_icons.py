"""`Teema käik` row actions: copy → edit → remove icons, and a kind named once.

Owner's UX corrections of 2026-10-26, fix 3. Every row's actions sit together
right after the record's label or link — the copy icon (a link to *this
history entry*), the pencil (that record's own correction) and the X (the
existing removal confirmation) — and only the actions the record and the reader
actually have are drawn. The standalone «Kopeeri link» at the foot of an open
row is gone. A koda.ee title that already opens with its kind no longer reads
«Ülevaade – Koduka ülevaade: …».
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse

from app.matters.enums import EngagementKind
from app.matters.headlines import kind_and_title
from app.matters.services import add_engagement, plan_website_overview, publish_website_overview
from tests import factories

pytestmark = pytest.mark.django_db

IMPORTED_OVERVIEW = (
    "Koduka ülevaade: Mida arvad üleriigilise planeeringu aastani 2050 uuendatud eelnõust?"
)
NEWS = "https://www.koda.ee/uudised/uleriigiline-planeering"
OVERVIEW = "https://www.koda.ee/meie-moju/hetkel-kasil/uleriigiline-planeering"


def _teema(matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


def _row(body: str, key) -> str:
    """The `<article>` holding the row whose line is `kaik-<key>-rida`."""
    at = body.index(f'id="kaik-{key}-rida"')
    start = body.rindex("<article", 0, at)
    return body[start : body.index("</article>", at)]


def _icons(row: str) -> list[str]:
    """The row's action icons in document order: copy, edit, remove."""
    return re.findall(r'class="uxtl__icon uxtl__icon--(copy|edit|remove)"', row)


def _published(matter, *, url: str, title: str):
    overview = plan_website_overview(matter=matter, actor=matter.owner)
    return publish_website_overview(
        overview=overview,
        url=url,
        published_on=dt.date(2026, 9, 9),
        actor=matter.owner,
        title=title,
    )


# ---------------------------------------------------------------------------
# The kind, once
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "title", "expected"),
    [
        (
            "Ülevaade",
            "Koduka ülevaade: Mida arvad üleriigilise planeeringu aastani 2050 uuendatud eelnõust?",
            "Ülevaade: Mida arvad üleriigilise planeeringu aastani 2050 uuendatud eelnõust?",
        ),
        (
            "Uudis",
            "Uudis: Saatsime 15 ettepanekut üleriigilise planeeringu eelnõu täiendamiseks",
            "Uudis: Saatsime 15 ettepanekut üleriigilise planeeringu eelnõu täiendamiseks",
        ),
        ("Uudis", "Koja uudis: Juristieksam", "Uudis: Juristieksam"),
        ("Ülevaade", "ÜLEVAADE: suurtähtedes", "Ülevaade: suurtähtedes"),
        # A real title that merely contains the word keeps every word.
        ("Ülevaade", "Majanduse ülevaade: 2026", "Ülevaade – Majanduse ülevaade: 2026"),
        ("Uudis", "Uudis kodulehel ilmus", "Uudis – Uudis kodulehel ilmus"),
        ("Uudis", "Hea uudis: eelnõu muudeti", "Uudis – Hea uudis: eelnõu muudeti"),
        ("Uudis", "Koja seisukoht VTK kohta", "Uudis – Koja seisukoht VTK kohta"),
        ("Uudis", "Uudis:", "Uudis"),
        ("Uudis", "", "Uudis"),
        ("Kaasamine", "Kaasamine: liikmed", "Kaasamine: liikmed"),
    ],
)
def test_the_kind_is_named_once_and_a_real_title_keeps_its_words(kind, title, expected):
    separator = ": " if kind == "Kaasamine" else " – "
    assert kind_and_title(kind, title, separator=separator) == expected


def test_an_imported_overview_and_news_read_their_kind_once(signed_in, normal_matter):
    overview = _published(
        normal_matter,
        url=OVERVIEW,
        title=IMPORTED_OVERVIEW,
    )
    news = _published(
        normal_matter,
        url=NEWS,
        title="Uudis: Saatsime 15 ettepanekut üleriigilise planeeringu eelnõu täiendamiseks",
    )

    body = signed_in.get(_teema(normal_matter)).content.decode()

    assert "Ülevaade: Mida arvad üleriigilise planeeringu aastani 2050" in body
    assert "Uudis: Saatsime 15 ettepanekut" in body
    assert "Koduka ülevaade" not in _row(body, overview.pk)
    assert "Uudis – Uudis" not in body and "Uudis: Uudis" not in body
    # Display only: what was stored is what was typed.
    overview.refresh_from_db()
    news.refresh_from_db()
    assert overview.title.startswith("Koduka ülevaade:")
    assert news.title.startswith("Uudis:")


def test_the_cleaned_headline_survives_a_correction_and_its_swap(signed_in, normal_matter):
    overview = _published(normal_matter, url=NEWS, title="Uudis: Vana pealkiri")
    response = signed_in.post(
        reverse(
            "matters:correct_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
        ),
        {
            "url": NEWS,
            "published_on": "09.09.2026",
            "title": "Uudis: Uus pealkiri",
            "revision": overview.revision_token,
        },
        HTTP_HX_REQUEST="true",
    )

    body = response.content.decode()
    assert 'hx-swap-oob="true">Uudis: Uus pealkiri<' in body, body[:400]
    assert "Uudis – Uudis" not in body


# ---------------------------------------------------------------------------
# The icons
# ---------------------------------------------------------------------------


def test_an_entry_carries_copy_edit_remove_in_that_order_and_no_text_link(
    signed_in, normal_matter, specialist
):
    entry = factories.EntryFactory(matter=normal_matter, author=specialist)

    row = _row(signed_in.get(_teema(normal_matter)).content.decode(), entry.pk)

    assert _icons(row) == ["copy", "edit", "remove"]
    assert ">Kopeeri link</button>" not in row
    assert "uxtl__permalink" not in row
    # One of each — never a second copy of a control.
    assert row.count("data-copy-href") == 1
    assert row.count('title="Muuda"') == 1
    assert row.count('title="Kustuta"') == 1


def test_the_copy_icon_copies_this_history_entry_never_its_web_link(signed_in, normal_matter):
    news = _published(normal_matter, url=NEWS, title="Uudis: Pealkiri")
    entry = factories.EntryFactory(
        matter=normal_matter, author=normal_matter.owner, kind="OTHER", url="https://example.org/x"
    )

    body = signed_in.get(_teema(normal_matter)).content.decode()

    for key in (news.pk, entry.pk):
        row = _row(body, key)
        (href,) = re.findall(r'data-copy-href="([^"]+)"', row)
        assert href == f"{_teema(normal_matter)}?kirje={key}#kaik-{key}-rida"
        assert "koda.ee" not in href and "example.org" not in href


def test_a_published_overviews_icons_follow_its_link(signed_in, normal_matter):
    news = _published(normal_matter, url=NEWS, title="Uudis: Pealkiri")

    row = _row(signed_in.get(_teema(normal_matter)).content.decode(), news.pk)

    link_at = row.index(f'id="kodulehe-ulevaade-{news.pk}-aadress"')
    assert _icons(row) == ["copy", "edit", "remove"]
    assert all(row.index(f"uxtl__icon--{name}") > link_at for name in ("copy", "edit", "remove"))
    # Not a second Kustuta on the head line.
    assert row.count('title="Kustuta"') == 1


def test_a_reader_gets_the_copy_icon_and_nothing_that_writes(client, reader, normal_matter):
    entry = factories.EntryFactory(matter=normal_matter, author=normal_matter.owner)
    news = _published(normal_matter, url=NEWS, title="Uudis: Pealkiri")
    client.force_login(reader)

    body = client.get(_teema(normal_matter)).content.decode()

    assert _icons(_row(body, entry.pk)) == ["copy"]
    assert _icons(_row(body, news.pk)) == ["copy"]


def test_a_closed_teema_keeps_copy_and_correction_but_not_removal(
    signed_in, normal_matter, specialist
):
    entry = factories.EntryFactory(matter=normal_matter, author=specialist)
    type(normal_matter).objects.filter(pk=normal_matter.pk).update(
        is_open=False, disposition="COMPLETED", closed_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
    )

    row = _row(signed_in.get(_teema(normal_matter)).content.decode(), entry.pk)

    # An entry's text may be corrected on a closed file (docs/adr/0076 §2);
    # removing it is new business work and is not offered.
    assert _icons(row) == ["copy", "edit"]


def test_an_engagement_head_carries_the_three_icons(signed_in, normal_matter, specialist):
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Kaasamine: liikmed",
        occurred_on=dt.date(2026, 9, 1),
        actor=specialist,
    )

    row = _row(signed_in.get(_teema(normal_matter)).content.decode(), engagement.pk)

    assert _icons(row) == ["copy", "edit", "remove"]
    assert "Kaasamine: Kaasamine" not in row
    # On the head line, beside the headline — not at the foot of the row.
    start = row.index('class="uxtl__head"')
    head = row[start : row.index("uxtl__mssub", start)] if "uxtl__mssub" in row else row[start:]
    assert _icons(head) == ["copy", "edit", "remove"]


def test_every_icon_has_a_word_and_a_tooltip(signed_in, normal_matter, specialist):
    factories.EntryFactory(matter=normal_matter, author=specialist)
    _published(normal_matter, url=NEWS, title="Uudis: Pealkiri")

    body = signed_in.get(_teema(normal_matter)).content.decode()

    for match in re.finditer(r'<(button|summary|a) class="uxtl__icon[^>]*>.*?</\1>', body, re.S):
        control = match.group(0)
        assert 'title="' in control, control
        assert "visually-hidden" in control or "aria-label=" in control, control
        assert 'aria-hidden="true"' in control, control
        assert "<button" not in control[1:] and "<a " not in control[1:], control
