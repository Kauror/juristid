"""Seed the seventeen reviewed Õigusakt types.

Reference data, not a fixture, and it arrives the way ``PolicyArea``'s did
(``taxonomy/0002``) rather than through the admin: ``Matter.legal_instruments``
points at these rows, a list assembled by hand in production is not reviewable,
and the ordering people see is part of what was reviewed.

**Where the seventeen came from.** A read-only survey of the historical register
— ``Tööd eelnõudega.xlsx``, column ``ÕIGUSAKT``, all sixteen year sheets from
2011 to 2026 — found 2418 non-empty cells in 58 distinct spellings. These
seventeen are what those 58 say once case, whitespace, diacritic and
abbreviation variants are collapsed: ``S``, ``seadus`` and ``Seadus`` are one
concept, not three. The reasoning per concept, and every alias, is in
``app.taxonomy.legal_instruments``.

**The baseline below is a frozen copy** of
``REFERENCE_LEGAL_INSTRUMENT_TYPES`` as reviewed today, not an import of it. A
historical migration that imports today's manifest changes meaning every time
the manifest is edited, so a database migrated last year would replay as
something else. ``tests/test_reference_legal_instruments.py`` asserts the two
agree, which is what keeps the copy honest: the *next* vocabulary change is a
new manifest entry plus a new migration, never an edit to this file.

**It fails closed.** A row already carrying one of these keys under a different
label, or one of these labels under a different key, is somebody's decision and
this migration will not overwrite or duplicate it. It raises instead — a silent
rename would move every Matter classified under it, and a silent duplicate would
make the label ambiguous everywhere it is matched by name.

**No Matter is classified here.** The reviewed reading of the historical column
exists (``canonical_legal_instrument_keys``) and is deliberately not applied to
any row: whether a source value may overwrite a person's own answer is a
precedence question nobody has decided, and deciding it inside a seed migration
would be deciding it in the wrong place (task §12, docs/adr/0070).
"""

from django.db import migrations

#: key, label, description, sort order. Frozen copy — see the module docstring.
BASELINE = [
    (
        "seadus",
        "Seadus",
        "Riigikogu vastu võetav seadus või selle muutmine.",
        10,
    ),
    (
        "maarus",
        "Määrus",
        (
            "Vabariigi Valitsuse või ministri määrus. Euroopa Liidu määrus on eraldi "
            "liik EL määrus."
        ),
        20,
    ),
    (
        "vtk",
        "VTK",
        "Väljatöötamiskavatsus — eelnõule eelnev kavatsus ja selle materjalid.",
        30,
    ),
    (
        "eelnou",
        "Eelnõu",
        (
            "Eelnõu, mille liiki allikas täpsemalt ei nimetanud. Kui liik on teada, "
            "vali ka see — üks teema võib kanda mõlemat."
        ),
        40,
    ),
    (
        "direktiiv",
        "Direktiiv",
        (
            "Euroopa Liidu direktiiv. Direktiiv on alati ELi akt, seega eraldi "
            "«EL direktiiv» liiki ei ole."
        ),
        50,
    ),
    (
        "el-maarus",
        "EL määrus",
        (
            "Euroopa Liidu määrus, sealhulgas Euroopa Komisjoni oma. Eraldi liik "
            "Määrusest: ELi määrus kehtib vahetult ja seda ei võeta üle."
        ),
        60,
    ),
    (
        "el-teatis",
        "EL teatis",
        "Euroopa Komisjoni teatis või muu ELi institutsiooni teatis.",
        70,
    ),
    (
        "konsultatsioon",
        "Konsultatsioon",
        (
            "Avalik konsultatsioon või arvamuse küsimine. Kas seda korraldab ELi "
            "institutsioon, ütleb menetlusliik, mitte akti liik."
        ),
        80,
    ),
    (
        "strateegia",
        "Strateegia",
        "Strateegiadokument. Arengukava ja tegevuskava on eraldi liigid.",
        90,
    ),
    (
        "arengukava",
        "Arengukava",
        "Riiklik või valdkondlik arengukava.",
        100,
    ),
    (
        "tegevuskava",
        "Tegevuskava",
        "Tegevuskava — kokkulepitud sammud, mitte õigusakt ega arengukava.",
        110,
    ),
    (
        "visioon",
        "Visioon",
        "Visioonidokument.",
        120,
    ),
    (
        "korraldus",
        "Korraldus",
        "Vabariigi Valitsuse või muu haldusorgani korraldus.",
        130,
    ),
    (
        "kaskkiri",
        "Käskkiri",
        "Ministri või muu haldusorgani käskkiri.",
        140,
    ),
    (
        "ettepanek",
        "Ettepanek",
        "Ettepanek, sealhulgas Euroopa Komisjoni ettepanek õigusakti kohta.",
        150,
    ),
    (
        "kusitlus",
        "Küsitlus",
        "Küsitlus või uuring, millele Koda vastab.",
        160,
    ),
    (
        "muu",
        "Muu",
        (
            "Mõni muu akt või dokument. Vali ka «Õigusakti liik» ja kirjuta, "
            "millega on tegemist — sellest ei teki uut liiki."
        ),
        200,
    ),
]


def _same_label(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def seed(apps, schema_editor):
    LegalInstrumentType = apps.get_model("taxonomy", "LegalInstrumentType")

    keys = [key for key, *_ in BASELINE]
    by_key = {item.key: item for item in LegalInstrumentType.objects.filter(key__in=keys)}
    # Read once, and every conflict is checked before anything is written, so a
    # refusal stops the whole vocabulary rather than leaving half of it.
    existing = list(LegalInstrumentType.objects.all())

    for key, label, _description, _sort_order in BASELINE:
        current = by_key.get(key)
        if current is not None and not _same_label(current.label_et, label):
            raise RuntimeError(
                f"LegalInstrumentType {key!r} already exists as {current.label_et!r}, and the "
                f"reviewed baseline calls it {label!r}. Renaming it here would move every "
                "Matter classified under it. Resolve the two by hand, then re-run the migration."
            )
        clash = next(
            (item for item in existing if item.key != key and _same_label(item.label_et, label)),
            None,
        )
        if clash is not None:
            raise RuntimeError(
                f"LegalInstrumentType {clash.key!r} is already named {clash.label_et!r}, which "
                f"the reviewed baseline uses for key {key!r}. Two types sharing a label make "
                "every label-based match ambiguous. Resolve the two by hand, then re-run."
            )

    for key, label, description, sort_order in BASELINE:
        if key in by_key:
            # Matches by identity. Left exactly as it is — description and sort
            # order are somebody's to edit, and topping them up here would make
            # a re-run of the migration a quiet content change.
            continue
        LegalInstrumentType.objects.create(
            key=key,
            label_et=label,
            description=description,
            sort_order=sort_order,
            is_active=True,
        )


def unseed(apps, schema_editor):
    """Remove only the rows this migration could have created, untouched.

    A reverse that deleted every row with a matching key would take a Matter's
    classification with it, and one that deleted nothing at all is a reverse
    nobody can use. So: pristine, unreferenced rows go, everything else stays.
    """
    LegalInstrumentType = apps.get_model("taxonomy", "LegalInstrumentType")

    for key, label, description, sort_order in BASELINE:
        item = LegalInstrumentType.objects.filter(key=key, matters__isnull=True).first()
        if item is None:
            continue
        pristine = (
            item.label_et == label
            and item.description == description
            and item.sort_order == sort_order
            and item.is_active
        )
        if pristine:
            item.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("taxonomy", "0005_legal_instrument_type"),
        # The reverse asks whether a Matter points at a type, so the join table
        # has to exist by the time this runs in either direction.
        ("matters", "0015_matter_legal_instruments"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
