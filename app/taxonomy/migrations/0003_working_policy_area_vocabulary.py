"""The working Valdkonnad vocabulary — version 2.0.

Version 1.0 seeded the nine public focus areas from koda.ee (``taxonomy/0002``).
This one seeds the twenty-three labels the department actually files under, and
it is deliberately additive on both sides of that sentence.

**Four of the nine survive untouched.** Energeetika, Riigihanked, Äriõigus and
Keskkond appear in the new list under exactly the same name, so they keep their
key, their row, their primary key and every ``Matter.policy_areas`` relation
pointing at them — including the seventy-one applied from the OneNote filing
structure. This migration does not touch those four rows at all.

**The other five are deactivated, not remapped.** Maksud, Tööjõud,
Halduskoormus, Aus konkurents and Haridus ja ettevõtlikkus carry names the new
list does not contain. There is no reviewed equivalence between any of them and
any new label — "Maksud" is not "Maksud ja toll", and "Haridus ja
ettevõtlikkus" is not "Haridus" — so nothing is reassigned. The rows stay, the
relations stay, the statistics still count them and the Matters filed under them
still show them. ``is_active`` goes false, which is the one thing that changes:
they stop being offered for *new* filing.

**The baseline below is frozen on purpose**, exactly as ``taxonomy/0002``'s is.
It is a literal copy of ``app.taxonomy.reference_data.REFERENCE_POLICY_AREAS_V2``
as reviewed today, not an import of it, so a database migrated under this
manifest replays as this manifest and not as whatever the manifest says later.
``tests/test_reference_policy_areas.py`` asserts the two agree.

**It fails closed**, again exactly as 0002 does: a row already carrying one of
these keys under a different name, or one of these names under a different
active key, is somebody's decision and this will not overwrite or duplicate it.
"""

from django.db import migrations

#: key, name, description, sort order. Frozen copy — see the module docstring.
BASELINE = [
    (
        "maksejouetus",
        "Maksejõuetus",
        (
            "Maksejõuetusmenetlus, pankrot, saneerimine ja võlgade "
            "ümberkujundamine. Äriühingu enda õiguslik raamistik on "
            "Äriõigus."
        ),
        10,
    ),
    (
        "raamatupidamine",
        "Raamatupidamine",
        (
            "Raamatupidamine, majandusaasta aruandlus ja auditeerimine. "
            "Maksuaruandlus on Maksud ja toll."
        ),
        20,
    ),
    (
        "intellektuaalomand",
        "Intellektuaalomand",
        ("Autoriõigus, patendid, kaubamärgid, ärisaladus ja litsentsimine."),
        30,
    ),
    (
        "toetusmeetmed",
        "Toetusmeetmed",
        ("Riigiabi, ettevõtlustoetused ja Euroopa Liidu rahastusmeetmed ning nende tingimused."),
        40,
    ),
    (
        "koalitsioonilepped",
        "Koalitsioonilepped",
        ("Valitsuse koalitsioonilepped ja tegevusprogrammid ettevõtluskeskkonda puudutavas osas."),
        50,
    ),
    (
        "oigusloome",
        "Õigusloome",
        (
            "Õigusloome kvaliteet ja menetlus ise: kaasamise hea tava, "
            "mõjuanalüüs, jõustumisreeglid. Eelnõu sisu kuulub oma "
            "valdkonda."
        ),
        60,
    ),
    (
        "energeetika",
        "Energeetika",
        (
            "Energiaturg, varustuskindlus, võrgud ja taristu ning ettevõtja "
            "energiakulu. Kliimaeesmärk ise on Keskkond; sama eelnõu võib "
            "kuuluda mõlemasse."
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
        "haridus",
        "Haridus",
        (
            "Haridussüsteem, kutseharidus, oskused ja hariduse vastavus "
            "tööturu vajadustele. Töösuhte küsimused on Töösuhted, "
            "töökeskkond."
        ),
        90,
    ),
    (
        "tarbijakaitse",
        "Tarbijakaitse",
        ("Tarbija õigused, müügitingimused, garantii ja kaubandustavad."),
        100,
    ),
    (
        "alkohol-tubakas",
        "Alkohol, tubakas",
        (
            "Alkoholi, tubaka ja nendega sarnaste toodete käitlemine, "
            "müügipiirangud ja reklaam. Aktsiis ise on Maksud ja toll."
        ),
        110,
    ),
    (
        "digiteemad",
        "Digiteemad",
        ("Digilahendused, andmed, küberturvalisus, e-teenused ja tehisintellekt ettevõtja vaates."),
        120,
    ),
    (
        "finantsoigus-rahapesu",
        "Finantsõigus, rahapesu",
        (
            "Finantsteenused, makseteenused, krediit ning rahapesu ja "
            "terrorismi rahastamise tõkestamine."
        ),
        130,
    ),
    (
        "ehitus",
        "Ehitus",
        (
            "Ehitusõigus, planeerimine, ehitusload ja kinnisvara. "
            "Keskkonnamõju hindamine on Keskkond."
        ),
        140,
    ),
    (
        "arioigus",
        "Äriõigus",
        (
            "Äri- ja tsiviilõiguslik raamistik: äriühinguõigus, registrid, "
            "lepingu- ja võlaõigus. Maksejõuetus on oma valdkond."
        ),
        150,
    ),
    (
        "valistoojoud",
        "Välistööjõud",
        (
            "Välismaalase töötamine ja elamine Eestis: kvoot, load, "
            "lühiajaline töötamine, rändepoliitika."
        ),
        160,
    ),
    (
        "maksud-ja-toll",
        "Maksud ja toll",
        ("Maksud, aktsiisid, tollireeglid, maksuhaldus ja maksuaruandlus."),
        170,
    ),
    (
        "toosuhted-tookeskkond",
        "Töösuhted, töökeskkond",
        ("Tööõigus, töölepingud, töötasu, töö- ja puhkeaeg ning töötervishoid ja tööohutus."),
        180,
    ),
    (
        "keskkond",
        "Keskkond",
        (
            "Keskkonna-, kliima- ja ressursiregulatsioon: jäätmed ja "
            "pakend, vesi, keskkonnatasud, keskkonnamõju hindamine. "
            "Energiaturg on Energeetika."
        ),
        190,
    ),
    (
        "muud-teemad",
        "Muud teemad",
        (
            "Teemad, mis ei kuulu ühessegi loetletud valdkonda. Kasuta "
            "viimase võimalusena; täpsustus käib sildiga."
        ),
        200,
    ),
    (
        "eli-oiguse-ulevotmine",
        "ELi õiguse ülevõtmine",
        (
            "Direktiivide ja määruste ülevõtmine ning rakendamine Eesti "
            "õiguses, ülereguleerimise vältimine."
        ),
        210,
    ),
    (
        "olulised-tahtajad",
        "Olulised tähtajad",
        (
            "Valdkondadeülene jälgimisnimekiri: teemad, mille ajakava on "
            "Koja jaoks kriitiline. Taksonoomia silt, mitte ühe teema "
            "operatiivne tähtaeg."
        ),
        220,
    ),
    (
        "arengukavad-strateegiad",
        "Arengukavad, strateegiad",
        ("Riiklikud arengukavad, strateegiad ja pikaajalised kavad ning nende ettevõtlusmõju."),
        230,
    ),
]

#: Version 1.0 keys whose *name* the working vocabulary does not contain. Frozen
#: copy of ``RETIRED_POLICY_AREA_KEYS_V1``.
RETIRED = [
    "maksud",
    "toojoud",
    "halduskoormus",
    "aus-konkurents",
    "haridus-ettevotlikkus",
]


def _same_name(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def seed(apps, schema_editor):
    PolicyArea = apps.get_model("taxonomy", "PolicyArea")

    keys = [key for key, *_ in BASELINE]
    by_key = {area.key: area for area in PolicyArea.objects.filter(key__in=keys)}
    existing = list(PolicyArea.objects.all())

    # Every conflict is checked before anything is written, so a refusal stops
    # the whole vocabulary rather than leaving half of it.
    for key, name, _description, _sort_order in BASELINE:
        current = by_key.get(key)
        if current is not None and not _same_name(current.name_et, name):
            raise RuntimeError(
                f"PolicyArea {key!r} already exists as {current.name_et!r}, and the reviewed "
                f"working vocabulary calls it {name!r}. Renaming it here would move every "
                "Matter filed under it. Resolve the two by hand, then re-run the migration."
            )
        clash = next(
            (
                area
                for area in existing
                if area.key != key and area.is_active and _same_name(area.name_et, name)
            ),
            None,
        )
        if clash is not None:
            raise RuntimeError(
                f"PolicyArea {clash.key!r} is already named {clash.name_et!r}, which the "
                f"reviewed working vocabulary uses for key {key!r}. Two active areas sharing "
                "a name make every name-based match ambiguous. Resolve the two by hand."
            )

    for key, name, description, sort_order in BASELINE:
        if key in by_key:
            # Matches by identity: one of the four that carried over. Left
            # exactly as it is — its description and sort order are somebody's
            # to edit, and topping them up here would make a re-run a quiet
            # content change.
            continue
        PolicyArea.objects.create(
            key=key,
            name_et=name,
            description=description,
            is_active=True,
            sort_order=sort_order,
        )

    # Retire, never reassign. `update()` rather than a loop because there is
    # nothing to decide per row and nothing else on the row changes.
    PolicyArea.objects.filter(key__in=RETIRED, is_active=True).update(is_active=False)


def unseed(apps, schema_editor):
    """Undo what this migration did, and only where undoing is safe.

    Two halves. The nineteen rows it created go if they are pristine and
    nothing points at them — a delete that cascaded through
    ``Matter.policy_areas`` would take somebody's filing with it, which is a
    far worse outcome than a rollback leaving unused rows behind. And the five
    retired rows go back to active, because rolling the code back means version
    1.0's vocabulary is the one being offered again.
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

    PolicyArea.objects.filter(key__in=RETIRED).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("taxonomy", "0002_reference_policy_areas"),
        # The reverse asks whether a Matter points at an area, so the join
        # table has to exist by the time this runs in either direction.
        ("matters", "0003_entry"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
