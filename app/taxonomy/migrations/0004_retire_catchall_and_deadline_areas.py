"""Withdraw two Valdkonnad from the working vocabulary — version 3.0.

Version 2.0 seeded the twenty-three labels the department named
(``taxonomy/0003``). Hands-on use found two of them doing damage rather than
work, and the product owner withdrew both with the approved Uus teema design on
2026-08-25.

**``olulised-tahtajad`` was never a subject area.** It named a cross-cutting
watch list — files whose timing matters — which is a workflow property and not
an answer to *which area of law is this*. The product already holds that
concept under a different model: ``MatterImportantDate`` is an operational date
on one Matter, and the *Olulised tähtajad* calendar is built from those rows.
Nothing in this migration touches that calendar; it shares four words with the
taxonomy label and nothing else.

**``muud-teemad`` duplicated ``Muu``.** Uus teema has always carried a ``Muu``
affordance that reveals a free-text box, and that box is not a ``PolicyArea``
at all — it writes ``Matter.policy_area_other`` and creates no taxonomy row. A
label spelling the same idea gave two ways to answer *none of these*, one of
which recorded nothing about the file.

**Deactivated, never remapped, never deleted**, exactly as ``taxonomy/0003``
treated the five it retired. The rows stay, the relations stay, statistics
still count them, and the Teema header still offers them back under its
"varasem valdkond" note so that correcting one field on an old Matter cannot
silently drop its filing. In particular nothing rewrites ``Muud teemad`` to
``Muu``: that is a guess about somebody else's judgement, and the free-text box
it would have to fill has nothing truthful to put in it (Uus teema redesign
§7.2).

**The keys below are a frozen copy** of
``app.taxonomy.reference_data.RETIRED_POLICY_AREA_KEYS_V2`` as reviewed today,
not an import of it, so a database migrated under this manifest replays as this
manifest. ``tests/test_reference_data_foundation.py`` asserts the two agree.
"""

from django.db import migrations

#: Frozen copy — see the module docstring.
WITHDRAWN = ["muud-teemad", "olulised-tahtajad"]

#: The names those keys carry. Checked rather than assumed: a row whose name
#: somebody has since changed is somebody's decision, and deactivating it on the
#: strength of a key alone would be this migration overruling them silently.
WITHDRAWN_NAMES = {
    "muud-teemad": "Muud teemad",
    "olulised-tahtajad": "Olulised tähtajad",
}


def _same_name(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def retire(apps, schema_editor):
    PolicyArea = apps.get_model("taxonomy", "PolicyArea")

    # Checked before anything is written, so a refusal stops the whole change
    # rather than leaving half of it.
    for area in PolicyArea.objects.filter(key__in=WITHDRAWN):
        expected = WITHDRAWN_NAMES[area.key]
        if not _same_name(area.name_et, expected):
            raise RuntimeError(
                f"PolicyArea {area.key!r} is named {area.name_et!r} and the reviewed "
                f"vocabulary withdrew {expected!r}. Somebody has renamed it since; "
                "deactivating it here would overrule that decision without saying so. "
                "Resolve the two by hand, then re-run the migration."
            )

    PolicyArea.objects.filter(key__in=WITHDRAWN, is_active=True).update(is_active=False)


def unretire(apps, schema_editor):
    """Rolling the code back means version 2.0's vocabulary is offered again.

    Only ``is_active`` moves, in both directions. Nothing was created and
    nothing was reassigned, so there is nothing else to undo.
    """
    PolicyArea = apps.get_model("taxonomy", "PolicyArea")
    PolicyArea.objects.filter(key__in=WITHDRAWN).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("taxonomy", "0003_working_policy_area_vocabulary"),
    ]

    operations = [
        migrations.RunPython(retire, unretire),
    ]
