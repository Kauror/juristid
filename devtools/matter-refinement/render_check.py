"""Render the real Matter page and report its structure. Development tool.

Not a test. It is the fast loop while the refinement is being built: it asks the
ordinary route for an ordinary Matter and prints whether the zones, the swap
targets and the copy contract are where they should be.

    uv run python devtools/matter-refinement/render_check.py
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

#: Structure the refinement must produce, and the swap targets it must not lose.
PRESENT = [
    'class="factspanel"',
    'id="teema-faktid"',
    'id="kaasamine"',
    'id="seotud-materjalid"',
    'id="ajajoon"',
    'id="teema-vaade"',
    'id="teema-pais"',
    'id="jargmiseks-rida"',
    'id="ajalugu-loend"',
    "+ Lisa kaasamine",
]

#: Only on a Matter that has dated facts. The section renders when it holds
#: something, exactly as it did before — a Matter nobody has recorded a
#: milestone on gets no heading announcing the absence, and the first one is
#: added from the composer.
PRESENT_WITH_DATES = ["+ Lisa tähtaeg"]

#: Strings the approved design says are not on the page.
ABSENT = [
    "Ava ajajoon",
    "+ Lisa oluline tähtaeg",
    "Kaasamist ei ole kirja pandud",
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
        print(f"on a Matter with dated facts ({dated.title[:40]}):")
        dated_body = client.get(f"/teemad/{dated.pk}/").content.decode()
        for marker in PRESENT_WITH_DATES:
            ok = marker in dated_body
            print(f"  {'ok ' if ok else 'MISSING'}  {marker}")
            if not ok:
                failures.append(f"missing on a dated Matter: {marker}")

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
