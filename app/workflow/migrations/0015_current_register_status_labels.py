"""Read the department's current `Hetkeseis` spellings (docs/adr/0148 §2).

The workbook's `Hetkeseisu info` sheet now writes the labels the lawyers
reviewed for this application — «jõustumise ootel», «Eesti seisukoht
koostamisel», «ELi õiguse ülevõtmise ootel» and «rohkem ei tegele» — and the
2025 and 2026 sheets of the 08.10.26 snapshot use them. `workflow/0004` only
knows the older spellings, so those rows imported with no stage at all.

Four generic mappings, each to the stage whose reviewed label the spelling
already is. **Additive:** the older spellings keep their rows and their meaning,
and «rohkem pole tegevusi plaanis» stays the disposition it has always been read
as — docs/adr/0131 §9's «history is not reread». «Rohkem ei tegele» maps to the
`monitoring_stopped` stage because that is the current vocabulary going forward.

The copy below is frozen on purpose: a migration that imported
`app.workflow.vocabulary` would change meaning whenever that module did.
"""

from django.db import migrations

# raw workbook value -> stage key (frozen copy of CURRENT_LABEL_TO_STAGE)
CURRENT_LABEL_TO_STAGE = {
    "jõustumise ootel": "awaiting_entry",
    "Eesti seisukoht koostamisel": "estonian_eu_position",
    "ELi õiguse ülevõtmise ootel": "awaiting_transposition",
    "rohkem ei tegele": "monitoring_stopped",
}

REVIEWER = "Osakonna «Hetkeseisu info» leht, töövihik 08.10.26 (docs/adr/0148)"


def seed(apps, schema_editor):
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")
    LegacyStatusMapping = apps.get_model("workflow", "LegacyStatusMapping")

    for raw_label, stage_key in CURRENT_LABEL_TO_STAGE.items():
        stage = StageVocabulary.objects.filter(key=stage_key).first()
        if stage is None:
            # A database whose vocabulary was never seeded has nothing to point
            # at; the mapping waits for the stage rather than inventing one.
            continue
        LegacyStatusMapping.objects.update_or_create(
            raw_label=raw_label,
            source_era="",
            defaults={
                "stage": stage,
                "disposition": "",
                "reviewed_by": REVIEWER,
                "notes": "Osakonna praegune kirjapilt; vastab ülevaadatud etapi nimele.",
            },
        )


def unseed(apps, schema_editor):
    LegacyStatusMapping = apps.get_model("workflow", "LegacyStatusMapping")
    LegacyStatusMapping.objects.filter(
        raw_label__in=list(CURRENT_LABEL_TO_STAGE), source_era="", reviewed_by=REVIEWER
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("workflow", "0014_opinion_follow_up"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
