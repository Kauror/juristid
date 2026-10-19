"""Address helpers live in neutral modules, not in a model or a view (JARCH-005).

2026-10-17 audit, G15. Three things a service or another app needed had been
living where only a lazy import could reach them:

* the credential-safe host label was the private `MatterEngagement._hostname`,
  called across the class by three other models — now
  `app.core.web_addresses.display_host`, where the URL rules live;
* `opinions_url` was in `app.matters.views`, which `app.related_materials`
  could only import late — now `app.submissions.opinions`, beside the filter
  value it builds;
* the refusal `workspace._overview_for_engagement_link` raises was read out of
  `app.matters.forms` — now `app.matters.publication_kind`, beside the rule.

DB-free: every check here reads a module or an unsaved instance.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

from app.core.web_addresses import display_host
from app.matters import forms as matter_forms
from app.matters import publication_kind
from app.matters import views as matter_views
from app.matters.enums import ProceduralLinkKind
from app.matters.models import (
    EXTERNAL_POSITION_LINK_FALLBACK,
    Entry,
    Matter,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralLink,
)
from app.submissions.opinions import OPINION_ROLE_FILTER, opinions_url

ROOT = Path(__file__).resolve().parent.parent
CREDENTIALED = "https://kasutaja:salasona@Survey.Example.ee:8443/kysitlus?id=1"


# ---------------------------------------------------------------------------
# display_host — the F-2 rule, now public
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("", ""),
        (None, ""),
        ("https://www.Koda.ee/et/uudised/x", "www.koda.ee"),
        (CREDENTIALED, "survey.example.ee"),
        # No host at all: the fallback drops everything before the `@`.
        ("https://kasutaja:salasona@/tee", "/tee"),
        # A malformed authority is not a crash on a read path.
        ("https://[::1/katki", "[::1/katki"),
    ],
)
def test_display_host_names_the_host_and_never_the_credentials(url, host):
    assert display_host(url) == host
    assert "salasona" not in display_host(url)


def test_every_model_link_label_uses_it():
    """The four models that print a link by its host, through the one helper."""
    assert Entry(url=CREDENTIALED).link_label == "survey.example.ee"
    assert MatterEngagement(url=CREDENTIALED).link_label == "survey.example.ee"
    assert MatterExternalPosition(url=CREDENTIALED).link_label == "survey.example.ee"
    link = MatterProceduralLink(url=CREDENTIALED, kind=ProceduralLinkKind.choices[0][0], label="")
    assert link.display_label == "survey.example.ee"
    # Their own fallbacks are unchanged: an address with no readable host.
    assert Entry(url="https://:@").link_label == "Ava link"
    assert MatterExternalPosition(url="https://:@").link_label == EXTERNAL_POSITION_LINK_FALLBACK
    assert MatterExternalPosition(url="").link_label == ""


def test_engagement_search_terms_carry_no_credentials():
    engagement = MatterEngagement(url=CREDENTIALED, smaily_url="", alchemer_url="")
    terms = engagement.link_search_terms
    assert terms[0] == "survey.example.ee"
    assert not any("salasona" in term or "kasutaja" in term for term in terms)


def test_the_private_model_helper_is_gone():
    assert not hasattr(MatterEngagement, "_hostname")


# ---------------------------------------------------------------------------
# opinions_url — one builder, importable without the views
# ---------------------------------------------------------------------------


def test_opinions_url_is_built_beside_its_filter():
    matter = Matter(pk=uuid.UUID("00000000-0000-0000-0000-000000000001"))
    page = f"/teemad/{matter.pk}/dokumendid/?roll={OPINION_ROLE_FILTER}"

    assert opinions_url(matter) == page
    assert opinions_url(matter, anchor="dokument-1") == f"{page}#dokument-1"


def test_the_views_name_is_the_same_function():
    """`app.matters.views.opinions_url` still answers, for its own routes."""
    assert matter_views.opinions_url is opinions_url


# ---------------------------------------------------------------------------
# The refusal sentence and the imports it frees
# ---------------------------------------------------------------------------


def test_the_news_refusal_is_one_constant():
    assert matter_forms.ENGAGEMENT_OVERVIEW_LINK_IS_NEWS is (
        publication_kind.ENGAGEMENT_OVERVIEW_LINK_IS_NEWS
    )


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize(
    ("module", "forbidden"),
    [
        ("app/related_materials/engine.py", "app.matters.views"),
        ("app/related_materials/selectors.py", "app.matters.views"),
        ("app/matters/workspace.py", "app.matters.forms"),
        ("app/search/views.py", "app.matters.views"),
    ],
)
def test_no_layer_reaches_up_for_these_helpers(module, forbidden):
    """A service or another app no longer imports a view or a form for them."""
    assert forbidden not in _imported_modules(ROOT / module)
