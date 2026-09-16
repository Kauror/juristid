"""Withdraw two more Valdkonnad from the working vocabulary — version 4.0.

Version 3.0 offered twenty-one areas (``taxonomy/0004``). The first structured
feedback from the lawyers using the demo withdrew two of them on 2026-09-16, and
both had the same defect: the label answers a question ``Valdkond`` does not ask.

**``koalitsioonilepped`` names a document, not an area of law.** A coalition
agreement is a *source* — a thing a Matter is about — and the product already
has the field for that answer: ``Õigusakt`` carries the reviewed instrument
vocabulary (``docs/adr/0070``). A tax measure announced in a coalition agreement
is Maksud ja toll; filing it under the agreement is how it leaves the tax
report.

**``eli-oiguse-ulevotmine`` is Menetlusliik, spelled twice.** ``Track`` has
carried ``NATIONAL_TRANSPOSITION`` under the label «ELi õiguse ülevõtmine» since
``matters/0001``, and that is the field answering *what kind of procedure is
this*. The same four words in the subject vocabulary made a directive about
construction get filed under the procedure instead of under Ehitus, and the two
answers then disagreed about one file.

**This migration touches the area and nothing else.** ``Matter.track`` is not
read, not written and not inferred here. In particular nothing sets the
``NATIONAL_TRANSPOSITION`` track on a Matter filed under the withdrawn area:
a Matter may already carry both, may carry neither, and deriving one from the
other would write a classification nobody reviewed — which is precisely the
"fuzzy migration over somebody else's judgement" ``taxonomy/0003`` and
``taxonomy/0004`` each refused in turn (docs/adr/0088 §3).

**Deactivated, never remapped, never deleted.** The rows stay, the relations
stay, statistics still count them, and the Teema header still offers them back
under its "varasem valdkond" note so that correcting one field on an old Matter
cannot silently drop its filing. ``MatterEditForm`` validates against the whole
table and offers the active vocabulary *plus whatever this Matter already
carries*, which is what makes that promise true rather than aspirational
(``app/matters/forms.py``).

**The keys below are a frozen copy** of
``app.taxonomy.reference_data.RETIRED_POLICY_AREA_KEYS_V3`` as reviewed today,
not an import of it, so a database migrated under this manifest replays as this
manifest. ``tests/test_reference_data_foundation.py`` asserts the two agree.
"""

from django.db import migrations

#: Frozen copy — see the module docstring.
WITHDRAWN = ["koalitsioonilepped", "eli-oiguse-ulevotmine"]

#: The names those keys carry. Checked rather than assumed: a row whose name
#: somebody has since changed is somebody's decision, and deactivating it on the
#: strength of a key alone would be this migration overruling them silently.
WITHDRAWN_NAMES = {
    "koalitsioonilepped": "Koalitsioonilepped",
    "eli-oiguse-ulevotmine": "ELi õiguse ülevõtmine",
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
    """Rolling the code back means version 3.0's vocabulary is offered again.

    Only ``is_active`` moves, in both directions. Nothing was created and
    nothing was reassigned, so there is nothing else to undo.
    """
    PolicyArea = apps.get_model("taxonomy", "PolicyArea")
    PolicyArea.objects.filter(key__in=WITHDRAWN).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("taxonomy", "0006_seed_legal_instrument_types"),
    ]

    operations = [
        migrations.RunPython(retire, unretire),
    ]
