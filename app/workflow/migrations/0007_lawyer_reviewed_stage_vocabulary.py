"""The lawyers' reviewed `Hetkeseis` vocabulary — version 2.0.

Three labels are reworded. Nothing is added, nothing is retired, nothing is
remapped and no schema changes. The reasoning is in
``app.workflow.reference_stages``; what is below is the frozen copy of it.

**Frozen, not imported.** A historical migration that read today's manifest
would replay as something else every time the manifest is edited, so the three
new labels are written out here. ``tests/test_reference_stages.py`` asserts the
copy and the manifest agree, which is what keeps the copy honest.

**It fails closed.** A row whose label is not the one ``workflow/0004`` seeded
has been edited by somebody, and rewording it here would overrule that decision
silently — so this leaves it exactly as it is. A reword is cosmetic and a
half-applied reword is visible in the UI, whereas refusing the whole migration
would block a deployment over a word.

**No Matter is touched.** ``Matter.stage`` is not read and not written, and this
migration reads and writes ``workflow`` and nothing else — it declares no
dependency on another app and queries none. A data migration that asks another
app a question in its *reverse* direction asks it of whatever state that app
happens to be rewound to, which is how an earlier draft of this file died on
``migrate <app> zero``.

**`is_provisional` is cleared, and that is a claim rather than tidying.**
``workflow/0004`` set it on all ten with a stated condition: *until the
department head and the lawyers have reviewed the stage vocabulary*. That review
is what this migration carries, so the flag comes off. Only rows that still
carry the reviewed baseline are touched, for the reason the rewording is
guarded.

**The reverse is exact.** It restores the three version-1.0 labels and puts
``is_provisional`` back on, under the same guards. There is nothing else to
undo, because there is nothing else to do.

**`Rohkem ei tegele` is deliberately not seeded here.** The lawyers asked for it
as a Hetkeseis and it is not one: *Koda has stopped working on this* is
``Disposition.MONITORING_STOPPED``, which the product has modelled separately
since ADR 0032 and which ``workflow/0004`` already reads the workbook's own
``rohkem pole tegevusi plaanis`` as.
"""

from django.db import migrations

#: key -> (label seeded by `workflow/0004`, label reviewed 2026-09-17).
#: Frozen copy — see the module docstring.
REWORDED = {
    "awaiting_entry": ("Ootan jõustumist", "Jõustumise ootel"),
    "estonian_eu_position": ("Eesti seisukoht", "Eesti seisukoht koostamisel"),
    "awaiting_transposition": ("Ootan ELi õiguse ülevõtmist", "ELi õiguse ülevõtmise ootel"),
}

#: Every key `workflow/0004` seeded, so the `is_provisional` clearing below
#: touches the reviewed vocabulary and nothing a later migration added.
SEEDED_KEYS = [
    "idea",
    "consultation",
    "government",
    "parliament",
    "awaiting_entry",
    "in_force",
    "estonian_eu_position",
    "eu_procedure",
    "awaiting_transposition",
    "other",
]


def adopt(apps, schema_editor):
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")

    for key, (seeded, reviewed) in REWORDED.items():
        StageVocabulary.objects.filter(key=key, label_et=seeded).update(label_et=reviewed)

    # The review `workflow/0004` was waiting for has happened.
    StageVocabulary.objects.filter(key__in=SEEDED_KEYS, is_provisional=True).update(
        is_provisional=False
    )


def revert(apps, schema_editor):
    """Version 1.0's wording again, and the flag back on."""
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")

    for key, (seeded, reviewed) in REWORDED.items():
        StageVocabulary.objects.filter(key=key, label_et=reviewed).update(label_et=seeded)

    StageVocabulary.objects.filter(key__in=SEEDED_KEYS, is_provisional=False).update(
        is_provisional=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("workflow", "0006_stage_help_from_the_department"),
    ]

    operations = [
        migrations.RunPython(adopt, revert),
    ]
