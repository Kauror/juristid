"""The lawyers' reviewed `Hetkeseis` vocabulary — version 2.0.

Three labels are reworded, one stage is added, nothing is retired and nothing is
remapped. The reasoning per change is in
``app.workflow.reference_stages``; what is below is the frozen copy of it.

**Frozen, not imported.** A historical migration that read today's manifest
would replay as something else every time the manifest is edited, so the three
new labels and the new stage are written out here.
``tests/test_reference_stages.py`` asserts the copy and the manifest agree,
which is what keeps the copy honest.

**It fails closed, in both halves.** A row whose label is not the one
``workflow/0004`` seeded has been edited by somebody, and rewording it here
would overrule that decision silently — so this leaves it exactly as it is
rather than raising: a reword is cosmetic and a half-applied reword is visible
in the UI, whereas refusing the whole migration would block a deployment over a
word. The *addition* does raise, because a row already holding the new key under
a different label, or the new label under a different key, would make every
label-based match ambiguous.

**No Matter is reclassified.** ``Matter.stage`` is not read and not written
here. In particular nothing moves a Matter onto ``no_further_work``: the
historical ``rohkem pole tegevusi plaanis`` rows are read as the
``MONITORING_STOPPED`` disposition by ``workflow/0004`` and keep that reading,
because a disposition and a stage are not the same claim
(``app/workflow/vocabulary.py``).

**`is_provisional` is cleared, and that is a claim rather than tidying.**
``workflow/0004`` set it on all ten with a stated condition: *until the
department head and the lawyers have reviewed the stage vocabulary*. That review
is what this migration carries, so the flag comes off — on the ten and on the
new eleventh. Only rows that still carry the reviewed baseline are touched, for
the reason the rewording is guarded.

**The reverse deactivates the new stage rather than deleting it**, and that is
a decision rather than caution.

A reverse may not delete a row a lawyer has already classified a file with, so
it would have to ask whether any ``Matter`` stands in the stage — and a
migration that queries another app's model in its *reverse* direction is asking
it of whatever historical state that app happens to be in at the time.
``migrate <app> zero`` rewinds ``matters`` past the state this migration was
written against before it gets here, and the question then fails outright:
``Cannot query "StageVocabulary object": Must be "StageVocabulary" instance``.
CI found that; it is not hypothetical.

So this migration reads and writes ``workflow`` and nothing else. Rolling it
back leaves ``no_further_work`` as a row nobody is offered — which is exactly
what the vocabulary's own retirement mechanism produces, destroys nothing, and
keeps every Matter that already holds it able to keep it (docs/adr/0032
§Amendment). Re-applying reactivates the same row rather than creating a second
one.

The reverse is otherwise exact: it restores the three version-1.0 labels and
puts ``is_provisional`` back on, under the same guard the forward direction
uses.
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

NEW_KEY = "no_further_work"
NEW_LABEL = "Rohkem ei tegele"
NEW_SORT_ORDER = 95
NEW_HELP = (
    "Koda ei kavatse selle teemaga enam aktiivselt tegeleda. See on hetkeseis, "
    "mitte teema lõpetamine: teema jääb avatuks ja selle sulgemine on eraldi "
    "otsus («Lõpeta teema»)."
)


def _same_label(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def adopt(apps, schema_editor):
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")

    # Checked before anything is written, so a refusal stops the whole change
    # rather than leaving half of it.
    existing = list(StageVocabulary.objects.all())
    held = next((stage for stage in existing if stage.key == NEW_KEY), None)
    if held is not None and not _same_label(held.label_et, NEW_LABEL):
        raise RuntimeError(
            f"StageVocabulary {NEW_KEY!r} already exists as {held.label_et!r}, and the "
            f"reviewed vocabulary calls it {NEW_LABEL!r}. Renaming it here would move "
            "every Matter standing in it. Resolve the two by hand, then re-run."
        )
    clash = next(
        (
            stage
            for stage in existing
            if stage.key != NEW_KEY and _same_label(stage.label_et, NEW_LABEL)
        ),
        None,
    )
    if clash is not None:
        raise RuntimeError(
            f"StageVocabulary {clash.key!r} is already named {clash.label_et!r}, which the "
            f"reviewed vocabulary uses for key {NEW_KEY!r}. Two stages sharing a label make "
            "every label-based match ambiguous. Resolve the two by hand, then re-run."
        )

    for key, (seeded, reviewed) in REWORDED.items():
        StageVocabulary.objects.filter(key=key, label_et=seeded).update(label_et=reviewed)

    # The review `workflow/0004` was waiting for has happened.
    StageVocabulary.objects.filter(key__in=SEEDED_KEYS, is_provisional=True).update(
        is_provisional=False
    )

    if held is None:
        StageVocabulary.objects.create(
            key=NEW_KEY,
            label_et=NEW_LABEL,
            help_text=NEW_HELP,
            sort_order=NEW_SORT_ORDER,
            is_active=True,
            is_provisional=False,
            # Empty means «applies to every Menetlusliik», which is the truth
            # here: a file of any kind can be one this office stops following.
            applicable_tracks=[],
        )
    else:
        # Re-applying after a rollback. The reverse deactivates this row rather
        # than deleting it, so the forward direction has to offer it again —
        # and only that, because everything else about the row may since have
        # been somebody's edit.
        StageVocabulary.objects.filter(pk=held.pk, is_active=False).update(is_active=True)


def revert(apps, schema_editor):
    """Version 1.0's wording again, and the new stage withdrawn rather than deleted."""
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")

    for key, (seeded, reviewed) in REWORDED.items():
        StageVocabulary.objects.filter(key=key, label_et=reviewed).update(label_et=seeded)

    StageVocabulary.objects.filter(key__in=SEEDED_KEYS, is_provisional=False).update(
        is_provisional=True
    )

    # Deactivated, never deleted — see the module docstring. A rollback may not
    # delete a fact somebody recorded, and `stages_including` keeps a withdrawn
    # stage readable and editable on the Matters standing in it (docs/adr/0032
    # §Amendment).
    StageVocabulary.objects.filter(key=NEW_KEY).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("workflow", "0006_stage_help_from_the_department"),
    ]

    operations = [
        migrations.RunPython(adopt, revert),
    ]
