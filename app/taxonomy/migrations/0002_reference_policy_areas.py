"""Seed the nine reviewed Koda policy areas.

The vocabulary is reference data, so it arrives the way the stage vocabulary
did (``workflow/0004``) rather than through the admin: a classification list
assembled by hand in production is not reviewable, ``Matter.policy_areas``
points at these rows, and every yearly report is cut along them.

**The baseline below is frozen on purpose.** It is a literal copy of
``app.taxonomy.reference_data.REFERENCE_POLICY_AREAS_V1`` as reviewed today, not
an import of it. A historical migration that imports today's manifest changes
meaning every time the manifest is edited, so a database migrated last year
would replay as something else. ``tests/test_reference_policy_areas.py`` asserts
the two agree, which is what keeps the copy honest: the *next* vocabulary change
is a new manifest entry plus a new migration, never an edit to this file.

**It fails closed.** A row already carrying one of these keys under a different
name, or one of these names under a different key, is somebody's decision and
this migration will not overwrite or duplicate it. It raises instead, because a
silent rename would move every Matter filed under that area and a silent
duplicate would make the name ambiguous everywhere it is matched by name — which
is exactly how the OneNote enrichment resolves sections.

Where production is concerned this is theory: ``PolicyArea`` is empty there. The
guard exists for the deployments where it is not.
"""

from django.db import migrations

#: key, name, description, sort order. Frozen copy — see the module docstring.
BASELINE = [
    (
        "maksud",
        "Maksud",
        (
            "Maksud, maksuhaldus ja maksutaolised tasud. Siia kuulub ka "
            "maksumenetlus ja maksuaruandlus ise; puhtalt aruandluskoormuse "
            "küsimus ilma maksusisuta on Halduskoormus."
        ),
        10,
    ),
    (
        "toojoud",
        "Tööjõud",
        (
            "Tööõigus, töösuhted, kutse- ja kvalifikatsiooninõuded ning "
            "välistööjõud. Oskuste ja õppe pool kuulub Hariduse ja "
            "ettevõtlikkuse alla."
        ),
        20,
    ),
    (
        "keskkond",
        "Keskkond",
        (
            "Keskkonna-, kliima- ja ressursiregulatsioon: jäätmed ja pakend, "
            "vesi, keskkonnatasud, keskkonnamõju hindamine. Energiaturu ja "
            "varustuskindluse küsimused on Energeetika."
        ),
        30,
    ),
    (
        "energeetika",
        "Energeetika",
        (
            "Energiaturg, varustuskindlus, võrgud ja taristu ning ettevõtja "
            "energiakulu. Kliimaeesmärk ise on Keskkond; sama eelnõu võib "
            "kuuluda mõlemasse."
        ),
        40,
    ),
    (
        "halduskoormus",
        "Halduskoormus",
        (
            "Läbiv aruandlus-, menetlus- ja bürokraatiakoormus: uued kohustused, "
            "nende kaotamine ja piirmäärad. Valdkonnaülene — enamasti koos selle "
            "valdkonnaga, mille koormusest jutt käib."
        ),
        50,
    ),
    (
        "aus-konkurents",
        "Aus konkurents",
        (
            "Aus konkurentsiolukord: varimajandus, ebavõrdsed tingimused, "
            "turu läbipaistvus ja järelevalve. Käsitleb turgu tervikuna, mitte "
            "üksikut vaidlust."
        ),
        60,
    ),
    (
        "arioigus",
        "Äriõigus",
        (
            "Äri- ja tsiviilõiguslik raamistik: äriühinguõigus, registrid, "
            "lepingu- ja võlaõigus, maksejõuetus. Maksuõigus on Maksud."
        ),
        70,
    ),
    (
        "riigihanked",
        "Riigihanked",
        (
            "Riigihangete regulatsioon ja praktika: hankekord, vaidlustus, "
            "hankija ja pakkuja kohustused."
        ),
        80,
    ),
    (
        "haridus-ettevotlikkus",
        "Haridus ja ettevõtlikkus",
        (
            "Haridussüsteem, oskused, ettevõtlikkus ja hariduse vastavus "
            "tööturu vajadustele. Tööõiguse ja töösuhte küsimused on Tööjõud."
        ),
        90,
    ),
]


def _same_name(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def seed(apps, schema_editor):
    PolicyArea = apps.get_model("taxonomy", "PolicyArea")

    keys = [key for key, *_ in BASELINE]
    by_key = {area.key: area for area in PolicyArea.objects.filter(key__in=keys)}
    # Every existing row that already carries a reviewed name, whatever key it
    # has. Read once, and every conflict is checked before anything is written,
    # so a refusal stops the whole vocabulary rather than leaving half of it.
    existing = list(PolicyArea.objects.all())

    for key, name, _description, _sort_order in BASELINE:
        current = by_key.get(key)
        if current is not None and not _same_name(current.name_et, name):
            raise RuntimeError(
                f"PolicyArea {key!r} already exists as {current.name_et!r}, and the reviewed "
                f"baseline calls it {name!r}. Renaming it here would move every Matter filed "
                "under it. Resolve the two by hand, then re-run the migration."
            )
        clash = next(
            (area for area in existing if area.key != key and _same_name(area.name_et, name)),
            None,
        )
        if clash is not None:
            raise RuntimeError(
                f"PolicyArea {clash.key!r} is already named {clash.name_et!r}, which the "
                f"reviewed baseline uses for key {key!r}. Two active areas sharing a name make "
                "every name-based match ambiguous. Resolve the two by hand, then re-run."
            )

    for key, name, description, sort_order in BASELINE:
        if key in by_key:
            # Matches by identity. Left exactly as it is — description and sort
            # order are somebody's to edit, and topping them up here would make
            # a re-run of the migration a quiet content change.
            continue
        PolicyArea.objects.create(
            key=key,
            name_et=name,
            description=description,
            is_active=True,
            sort_order=sort_order,
        )


def unseed(apps, schema_editor):
    """Remove only rows this migration created and nothing has used.

    A reverse that deleted the vocabulary outright would cascade through
    ``Matter.policy_areas`` and take the classification of every file with it —
    a far worse outcome than a rollback that leaves nine unused rows behind.

    Nor does it raise when a row is in use. Rolling the *code* back while real
    filing exists is the ordinary reason to run this, and a reverse that refuses
    exactly then is a reverse nobody can use. So: pristine, unreferenced rows go,
    everything else stays, and ``reference_data verify`` will say which.
    """
    PolicyArea = apps.get_model("taxonomy", "PolicyArea")

    for key, name, description, sort_order in BASELINE:
        area = PolicyArea.objects.filter(key=key, matters__isnull=True).first()
        if area is None:
            continue
        pristine = (
            area.name_et == name
            and area.description == description
            and area.sort_order == sort_order
            and area.is_active
        )
        if pristine:
            area.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("taxonomy", "0001_initial"),
        # The reverse asks whether a Matter points at an area, so the join table
        # has to exist by the time this runs in either direction.
        ("matters", "0003_entry"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
