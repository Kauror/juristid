"""Agent E (independent verifier): QA-05 on the shared-dossier surface (ADR 0208).

The owner's rule of 2026-11-05 is «one rule everywhere: no surface shows a
count, reference or title of a stage the reader cannot open». The round
converted the header, banner, rail and MCP (ADR 0207) and kept «Seotud
materjalid» silent (ADR 0209), but the OneNote page's «Ühine toimik» note
(ADR 0208, `templates/legacy_import/source_page.html`) still prints
«Lisaks on leht seotud N teemaga, mida selles kasutajavaates ei kuvata» and a
total «Ühine toimik · N teemat» that includes them — also when the hidden Teema
is a continuation stage between two readable ones.

Latent under ADR 0181 (no live reader is narrow, and a deleted Teema's link is
not part of the dossier). This test fails on a3e305a9 by design: it documents
the residual surface for the lead. Synthetic data only.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from tests.test_onenote_shared_dossiers import (  # noqa: F401  (fixtures)
    _also_link,
    _continues,
    _fresh_package_cache,
    _link,
    _restricted,
    enabled,
    operator,
    world,
)

pytestmark = pytest.mark.django_db


def test_e_a_hidden_continuation_stage_on_a_shared_page_is_not_counted(world, client, operator):  # noqa: F811
    hidden = _restricted()
    _also_link(hidden, 2)
    _continues(world["c"], hidden)  # c -> hidden -> d: the hidden Teema is a stage
    _continues(hidden, world["d"])
    client.force_login(operator)
    html = client.get(
        reverse("legacy_import:source_page", kwargs={"pk": _link(world["c"], 2).pk})
    ).content.decode()
    assert "SALAJANE-TEEMA-77" not in html and hidden.display_reference not in html
    flat = " ".join(html.split())
    # The rule: no number for what cannot be opened; the total counts readable Teemad.
    assert not re.search(r"Lisaks on leht seotud \d+ teemaga", flat), "hidden stage counted"
    assert "Ühine toimik · 3 teemat" not in flat, "the total includes the hidden stage"
