"""`/kiirklahvid/` — the one page that lists the keyboard shortcuts.

Four things are held here:

* **the door** is Uuendused's: no business data, so every signed-in user and a
  persona-less gate session read it, and the gate still stands in front of it;
* **the page is honest**: every row names the script that answers its key, and
  that script still says so (`app/core/keyboard_shortcuts.py`);
* **the screens carry no hint strips**: the search field lost its painted
  «Ctrl K» and kept the key, and no everyday template spells a key out;
* **the page reads as a document**: one `h1`, an `h2` per area, and tables with
  real column headers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from app.core.keyboard_shortcuts import GROUPS, MOD
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

URL = "/kiirklahvid/"
JS_DIR = Path(settings.BASE_DIR) / "static" / "js"
TEMPLATE_DIR = Path(settings.BASE_DIR) / "templates"


def _folded(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _footer(body: str) -> str:
    start = body.index('<footer class="app__footer">')
    return body[start : body.index("</footer>", start)]


# ---------------------------------------------------------------------------
# The route and who reads it
# ---------------------------------------------------------------------------


def test_the_route_is_kiirklahvid() -> None:
    assert reverse("core:keyboard_shortcuts") == URL


@pytest.mark.django_db
@pytest.mark.parametrize(
    "factory",
    [
        factories.ReaderFactory,
        factories.UserFactory,
        factories.DepartmentHeadFactory,
    ],
    ids=["reader", "specialist", "department-head"],
)
def test_every_ordinary_signed_in_user_reads_it(client: Client, factory) -> None:
    """No Haldus, no administrator role: a READER reads it like everyone else."""
    client.force_login(factory())
    response = client.get(URL)
    assert response.status_code == 200
    assert re.search(r"<h1[^>]*>\s*Kiirklahvid\s*</h1>", response.content.decode())


@pytest.mark.django_db
def test_the_shared_gate_still_stands_in_front_of_it(client, settings) -> None:
    """Refused before the password exactly as Uuendused is — no public bypass."""
    apply_shared_gate(settings)
    response = client.get(URL)
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:shared_gate")


@pytest.mark.django_db
def test_past_the_gate_it_needs_no_persona(client, settings) -> None:
    """Uuendused's door: nobody's work is on the page, so nobody is asked who they are."""
    apply_shared_gate(settings)
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    response = client.get(URL)
    assert response.status_code == 200
    assert not response.wsgi_request.user.is_authenticated


@pytest.mark.django_db
def test_a_named_persona_past_the_gate_reads_it_too(client, settings) -> None:
    apply_shared_gate(settings)
    client.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    reader = factories.ReaderFactory()
    client.force_login(reader)
    response = client.get(URL)
    assert response.status_code == 200
    assert response.wsgi_request.user.pk == reader.pk


@pytest.mark.django_db
def test_the_page_needs_no_database(client: Client, django_assert_num_queries) -> None:
    with django_assert_num_queries(0):
        assert client.get(URL).status_code == 200


# ---------------------------------------------------------------------------
# The footer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_footer_links_it_beside_uuendused(client: Client) -> None:
    footer = _footer(client.get("/uuendused/").content.decode())
    assert re.search(
        r'href="/uuendused/">Uuendused</a>\s*·\s*<a href="/kiirklahvid/">Kiirklahvid</a>', footer
    )


@pytest.mark.django_db
def test_the_footer_link_is_on_an_ordinary_work_page(client: Client) -> None:
    client.force_login(factories.ReaderFactory())
    footer = _footer(client.get(reverse("matters:my_work")).content.decode())
    assert f'href="{URL}"' in footer


@pytest.mark.django_db
def test_the_link_is_not_behind_haldus(client: Client) -> None:
    """On the footer every page has, and nowhere in the administration menu."""
    client.force_login(factories.ReaderFactory())
    body = client.get(reverse("matters:my_work")).content.decode()
    header = body[: body.index('<main class="app__main"')]
    assert f'href="{URL}"' not in header


# ---------------------------------------------------------------------------
# The page is honest about the code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "shortcut",
    [shortcut for group in GROUPS for shortcut in group.shortcuts],
    ids=[f"{group.slug}-{'+'.join(s.keys[0])}" for group in GROUPS for s in group.shortcuts],
)
def test_every_documented_key_is_answered_by_the_script_it_names(shortcut) -> None:
    """A row whose handler was deleted fails here, not in a lawyer's hands."""
    assert shortcut.evidence, "a documented shortcut must name the code that answers it"
    for filename, needle in shortcut.evidence:
        source = _folded((JS_DIR / filename).read_text(encoding="utf-8"))
        assert _folded(needle) in source, f"{filename} no longer contains {needle!r}"


def test_every_modifier_chord_is_read_as_ctrl_or_command_by_its_script() -> None:
    """The page promises ⌘ on macOS wherever it promises Ctrl; the handler must read both."""
    for group in GROUPS:
        for shortcut in group.shortcuts:
            if shortcut.uses_modifier:
                assert any(
                    "event.ctrlKey || event.metaKey" in needle for _, needle in shortcut.evidence
                ), shortcut.action


def test_the_known_shortcuts_are_all_documented() -> None:
    documented = {
        (group.slug, "+".join(chord))
        for group in GROUPS
        for shortcut in group.shortcuts
        for chord in shortcut.keys
    }
    for expected in [
        ("koikjal", f"{MOD}+K"),
        ("koikjal", "Esc"),
        ("teema", "L"),
        ("teema", f"{MOD}+Enter"),
        ("teema", "Esc"),
        ("minu-asjad", "J"),
        ("minu-asjad", "K"),
        ("minu-asjad", "Enter"),
    ]:
        assert expected in documented, expected


def test_the_page_renders_every_key_and_both_modifiers(client: Client) -> None:
    body = client.get(URL).content.decode()
    for group in GROUPS:
        assert group.title in body
        for shortcut in group.shortcuts:
            for chord in shortcut.windows:
                for key in chord:
                    assert f'<kbd class="key">{key}</kbd>' in body, key
    assert '<kbd class="key">Ctrl</kbd> + <kbd class="key">K</kbd>' in body
    assert '<kbd class="key">⌘</kbd> + <kbd class="key">K</kbd>' in body
    assert '<kbd class="key">⌘</kbd> + <kbd class="key">Enter</kbd>' in body


# ---------------------------------------------------------------------------
# The page reads as a document
# ---------------------------------------------------------------------------


def test_one_h1_and_an_h2_per_area(client: Client) -> None:
    body = client.get(URL).content.decode()
    main = body[body.index('<main class="app__main"') : body.index("</main>")]
    assert len(re.findall(r"<h1\b", main)) == 1
    assert len(re.findall(r"<h2\b", main)) == len(GROUPS)
    assert "<h3" not in main


def test_every_table_has_column_headers_a_caption_and_its_heading(client: Client) -> None:
    body = client.get(URL).content.decode()
    tables = re.findall(r"<table\b.*?</table>", body, re.S)
    assert len(tables) == len(GROUPS)
    for group, table in zip(GROUPS, tables, strict=True):
        assert re.search(r'<th scope="col"[^>]*>\s*Klahv\s*</th>', table)
        assert re.search(r'<th scope="col"[^>]*>\s*Mida teeb\s*</th>', table)
        assert "<caption" in table and group.title in table
    for group in GROUPS:
        heading = f'id="kiirklahvid-{group.slug}"'
        assert heading in body
        assert f'aria-labelledby="kiirklahvid-{group.slug}"' in body


# ---------------------------------------------------------------------------
# The screens carry no hint strips
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_search_field_keeps_its_shortcut_and_loses_the_painted_hint(client: Client) -> None:
    client.force_login(factories.ReaderFactory())
    body = client.get(reverse("matters:my_work")).content.decode()
    form = body[body.index('<form class="searchfield"') :]
    form = form[: form.index("</form>")]
    assert 'id="global-search"' in form
    assert 'aria-keyshortcuts="Control+K Meta+K"' in form
    assert "Ctrl K" not in form
    assert "<kbd" not in form
    assert "searchfield__hint" not in body
    # The handler the attribute announces is still there.
    assert 'document.getElementById("global-search")' in (JS_DIR / "app.js").read_text(
        encoding="utf-8"
    )


def test_no_everyday_template_spells_a_key_out() -> None:
    """Keys are written down on /kiirklahvid/ and nowhere else.

    A `<kbd>` on a working screen is a hint strip, which the owner asked to
    be gone (2026-10-25). Comments do not count: they are for maintainers.
    """
    offenders = []
    for path in sorted(TEMPLATE_DIR.rglob("*.html")):
        if path == TEMPLATE_DIR / "core" / "keyboard_shortcuts.html":
            continue
        text = re.sub(
            r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#.*?#}",
            "",
            path.read_text(encoding="utf-8"),
            flags=re.S,
        )
        if "<kbd" in text or "keyhint" in text:
            offenders.append(str(path.relative_to(TEMPLATE_DIR)))
    assert offenders == []
