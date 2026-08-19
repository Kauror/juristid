"""Seed the reviewed `Hetkeseis` vocabulary and its legacy mappings.

The live workbook carries **eleven** raw `HETKESEIS` values. Ten of them
describe where the external process stands and become procedural stages. The
eleventh, `rohkem pole tegevusi plaanis`, does not: it says Koda stopped
working on the file, which is a closure reason. Importing it as a stage would
have merged two different questions into one column — exactly the conflation the
product model separates (master specification 3.4, 11.2).

Two further points this migration deliberately encodes:

* `jõustunud` is a stage, not a closure. An act entering into force does not
  mean Koda's file is finished; monitoring implementation is ordinary work.
* Every mapping is seeded as a **generic** (era-less) row. The era-aware scheme
  from Stage 0 stays intact, and a year whose meaning turns out to differ gets
  its own row later without rewriting how earlier rows were already read.

The vocabulary is reference data, so it is seeded here rather than through the
admin: a stage list assembled by hand in production is not reviewable, and
`Matter.stage` points at these rows.
"""

from django.db import migrations

# key, Estonian label, help text, sort order
STAGES = [
    (
        "idea",
        "Idee",
        "Algatus on teada, kuid ametlikku menetlust ei ole veel alanud.",
        10,
    ),
    (
        "consultation",
        "Kooskõlastusringil",
        "Eelnõu on kooskõlastusringil ja arvamuse esitamise aeg on jooksmas.",
        20,
    ),
    (
        "government",
        "Valitsuses",
        "Eelnõu on valitsuse menetluses.",
        30,
    ),
    (
        "parliament",
        "Riigikogus",
        "Eelnõu on Riigikogu menetluses.",
        40,
    ),
    (
        "awaiting_entry",
        "Ootan jõustumist",
        "Akt on vastu võetud, kuid ei ole veel jõustunud.",
        50,
    ),
    (
        "in_force",
        "Jõustunud",
        (
            "Akt on jõustunud. See ei tähenda, et Koja töö on lõppenud — "
            "rakendamise jälgimine on tavaline töö ja teema sulgemine on eraldi otsus."
        ),
        60,
    ),
    (
        "estonian_eu_position",
        "Eesti seisukoht",
        "Kujundatakse Eesti seisukohta ELi algatuse kohta.",
        70,
    ),
    (
        "eu_procedure",
        "ELi menetluses",
        "Algatus on Euroopa Liidu institutsioonide menetluses.",
        80,
    ),
    (
        "awaiting_transposition",
        "Ootan ELi õiguse ülevõtmist",
        "ELi akt on vastu võetud ja oodatakse riigisisest ülevõtmist.",
        90,
    ),
    (
        "other",
        "Muu",
        "Menetlus ei sobi ühegi ülaltoodud etapi alla.",
        100,
    ),
]

# raw workbook value -> stage key
RAW_LABEL_TO_STAGE = {
    "idee": "idea",
    "kooskõlastusringil": "consultation",
    "valitsuses": "government",
    "Riigikogus": "parliament",
    "ootan jõustumist": "awaiting_entry",
    "jõustunud": "in_force",
    "Eesti seisukoht": "estonian_eu_position",
    "ELi menetluses": "eu_procedure",
    "ootan ELi õiguse ülevõtmist": "awaiting_transposition",
    "muu": "other",
}

# The one raw value that is a closure reason rather than a stage.
RAW_LABEL_TO_DISPOSITION = {
    "rohkem pole tegevusi plaanis": "MONITORING_STOPPED",
}

REVIEWER = "Stage-1 seeded from the live Tööd eelnõudega workbook vocabulary"


def seed(apps, schema_editor):
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")
    LegacyStatusMapping = apps.get_model("workflow", "LegacyStatusMapping")

    stages = {}
    for key, label, help_text, sort_order in STAGES:
        stage, _created = StageVocabulary.objects.update_or_create(
            key=key,
            defaults={
                "label_et": label,
                "help_text": help_text,
                "sort_order": sort_order,
                "is_active": True,
                # The list matches the live workbook, but the final wording,
                # help text and track applicability are still the department
                # head's call (docs/open-decisions.md).
                "is_provisional": True,
                "applicable_tracks": [],
            },
        )
        stages[key] = stage

    for raw_label, stage_key in RAW_LABEL_TO_STAGE.items():
        LegacyStatusMapping.objects.update_or_create(
            raw_label=raw_label,
            source_era="",
            defaults={
                "stage": stages[stage_key],
                "disposition": "",
                "reviewed_by": REVIEWER,
            },
        )

    for raw_label, disposition in RAW_LABEL_TO_DISPOSITION.items():
        LegacyStatusMapping.objects.update_or_create(
            raw_label=raw_label,
            source_era="",
            defaults={
                "stage": None,
                "disposition": disposition,
                "reviewed_by": REVIEWER,
                "notes": (
                    "Ei ole menetlusetapp: kirjeldab Koja töö lõpetamist, "
                    "mitte välise menetluse seisu."
                ),
            },
        )


def unseed(apps, schema_editor):
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")
    LegacyStatusMapping = apps.get_model("workflow", "LegacyStatusMapping")

    raw_labels = list(RAW_LABEL_TO_STAGE) + list(RAW_LABEL_TO_DISPOSITION)
    LegacyStatusMapping.objects.filter(raw_label__in=raw_labels, source_era="").delete()
    # Only removes stages nothing points at; a seeded stage in use is left
    # alone rather than taking Matters down with it.
    StageVocabulary.objects.filter(
        key__in=[key for key, *_ in STAGES], matters__isnull=True
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("workflow", "0003_nextaction"),
        ("matters", "0003_entry"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
