"""Render the real Teema page and report its structure. Development tool.

Not a test. It is the fast loop while the page is being built: it asks the
ordinary route for an ordinary Matter and prints whether the approved target's
zones, controls and swap targets are where they should be.

**The contract below is the approved target** (`TEEMA_TARGET.html`,
`TEEMA_TARGET_SPEC.md`, docs/adr/0074). It used to be the 2026-09 refinement's,
which is the page the target supersedes — a utility in the repository declaring
the old page approved is a second, wrong answer to "what does this page look
like", and those are the ones that get believed.

    uv run python devtools/teema-target/render_check.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import django

# Run from anywhere: the repository root is two directories up, and it is what
# `config` and `app` are importable from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
# The test client speaks to `testserver`, which a development `ALLOWED_HOSTS`
# has no reason to carry — without it every response here is a 400 that looks
# like a broken page.
os.environ["DJANGO_ALLOWED_HOSTS"] = (
    os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1") + ",testserver"
)
django.setup()

from django.test import Client  # noqa: E402

from app.accounts.models import User  # noqa: E402
from app.matters.models import Matter  # noqa: E402

#: Structure the approved target must produce, and the swap targets it keeps.
PRESENT = [
    'id="teema-vaade-wrap"',
    'id="teema-pais"',
    'id="teema-vaade"',
    'id="jargmiseks-rida"',
    "data-koostaja-toggle",
    'id="teema-koostaja"',
    # The five composer panels, in the target's order.
    'id="cx-tahtaeg"',
    'id="cx-joustumine"',
    'id="cx-toovoit"',
    'id="cx-kaasamine"',
    'id="cx-lopeta"',
    # The file affordance, always available.
    "cx-drop--corner",
    "Lohista fail siia või",
    'id="ajajoon"',
    'id="ajalugu-loend"',
    # The rail's four blocks.
    'id="teema-andmed"',
    'id="koja-arvamus"',
    'id="seotud-materjalid"',
    "railcard--note",
    'id="teema-markme-seis"',
]

#: Only on a Matter that has a milestone. The strip renders what exists and
#: nothing else, so a Matter with none draws no strip at all rather than an
#: empty grid under a heading (docs/adr/0074 §12).
PRESENT_WITH_MILESTONES = ["tl-strip", "tl-step__dot"]

#: Everything the approved target removed. Each of these was on the page the
#: refinement built, and each is a deliberate supersession rather than an
#: oversight (docs/adr/0074).
ABSENT = [
    'class="factspanel"',
    'id="teema-faktid"',
    'id="kaasamine"',
    "+ Lisa kaasamine",
    "+ Manus",
    "timelinefilter",
    "uxtl__preview",
    "uxtl__sysrow",
    "Lükka edasi",
    "Ava ajajoon",
    "Kaasamist ei ole kirja pandud",
    # Retired from this page only; the columns and the endpoints are untouched.
    "Muu valdkond",
    "Andmeklass",
    "Märgi testandmeteks",
]


def main() -> int:
    reader = User.objects.filter(upn="sandra@example.invalid").first()
    matter = (
        Matter.objects.filter(is_open=True)
        .exclude(title__startswith="Konf")
        .order_by("-created_at")
        .first()
    )
    if reader is None or matter is None:
        print("seed the world first: manage.py seed_e2e_data")
        return 1

    client = Client()
    client.force_login(reader)
    response = client.get(f"/teemad/{matter.pk}/")
    body = response.content.decode()

    print(f"GET /teemad/{matter.pk}/  ->  {response.status_code}")
    print(f"matter: {matter.title[:60]}")
    print()

    failures = []
    for marker in PRESENT:
        ok = marker in body
        print(f"  {'ok ' if ok else 'MISSING'}  {marker}")
        if not ok:
            failures.append(f"missing: {marker}")
    print()
    for marker in ABSENT:
        gone = marker not in body
        print(f"  {'ok ' if gone else 'PRESENT'}  (absent) {marker}")
        if not gone:
            failures.append(f"still present: {marker}")

    dated = (
        Matter.objects.filter(is_open=True, important_dates__isnull=False)
        .exclude(title__startswith="Konf")
        .distinct()
        .first()
    )
    if dated is not None:
        print()
        print(f"on a Matter with a milestone ({dated.title[:40]}):")
        dated_body = client.get(f"/teemad/{dated.pk}/").content.decode()
        for marker in PRESENT_WITH_MILESTONES:
            ok = marker in dated_body
            print(f"  {'ok ' if ok else 'MISSING'}  {marker}")
            if not ok:
                failures.append(f"missing on a Matter with a milestone: {marker}")

    print()
    print("composer:")
    # Open on an initial GET is the load-bearing one (docs/adr/0074 §3).
    tag = (
        body[body.index('id="teema-koostaja"') :].split(">")[0] if "teema-koostaja" in body else ""
    )
    opened = "open" in tag
    print(f"  {'ok ' if opened else 'CLOSED '} open on arrival")
    if not opened:
        failures.append("the composer is not open on arrival")
    chips = body.count('class="cx-panel"') + body.count("cx-panel cx-panel--last")
    print(f"  {'ok ' if chips == 5 else 'WRONG  '} {chips} progressive panels (want 5)")
    if chips != 5:
        failures.append(f"{chips} composer panels, not 5")
    saves = body.count("data-composer-submit")
    print(f"  {'ok ' if saves == 1 else 'WRONG  '} {saves} composer save button (want 1)")
    if saves != 1:
        failures.append(f"{saves} composer save buttons, not 1")

    print()
    print("header:")
    metaline = body[body.index('class="metaline"') : body.index('class="summary"')]
    order = [
        label
        for label in ("Vastutaja", "Valdkond", "Hetkeseis", "Saabus", "Tähtaeg")
        if label in metaline
    ]
    print(f"  metaline: {' · '.join(order)}")
    if order[:4] != ["Vastutaja", "Valdkond", "Hetkeseis", "Saabus"]:
        failures.append(f"metaline order is {order}")
    rail = body[body.index('id="teema-andmed"') : body.index("</aside>")]
    if "Saabus" in rail:
        failures.append("Saabus is still in the rail")

    print()
    if response.status_code != 200:
        failures.append(f"status {response.status_code}")
    if failures:
        print("FAILURES:")
        for line in failures:
            print(f"  {line}")
        return 1
    print("structure ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
