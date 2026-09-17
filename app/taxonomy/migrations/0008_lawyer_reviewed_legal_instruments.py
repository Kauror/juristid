"""The lawyers' reviewed `Õigusakt` vocabulary — version 2.0.

Twelve of ``taxonomy/0006``'s seventeen types stop being offered, five are new,
two are reused under a clearer name and the ten that remain are renumbered into
the reviewed order. The reasoning per decision is in
``app.taxonomy.legal_instruments``; what is below is the frozen copy of it.

**Frozen, not imported**, for the reason ``taxonomy/0006`` states: a historical
migration that read today's manifest would replay as something else every time
the manifest is edited. ``tests/test_reference_legal_instruments.py`` asserts
the copy and the manifest agree.

**Deactivated, never remapped, never deleted.** The twelve keep their row, their
key, their description, their relations, their place in every statistic and
their register filter; what changes is one boolean. Nothing guesses that a
Matter filed under ``strateegia`` meant the new combined row or that one filed
under ``konsultatsioon`` meant ``eli-konsultatsioon`` — the first is true of
some of them and the second is false of the domestic ones, and writing either
down would be a fuzzy migration over somebody else's judgement, which
``taxonomy/0003``, ``taxonomy/0004`` and ``taxonomy/0007`` each refused in turn.

**No Matter is reclassified and no ``Matter.track`` is written.** This migration
reads neither. Version 2.0 makes the domestic/EU group readable *from the type*
for Matters created after it, in the create form; it does not reach backwards
(docs/adr/0090 §4).

**It fails closed.** A row already carrying one of the new keys under a
different label, or one of the new labels under a different key, raises: a
silent rename would move every Matter classified under it and a silent duplicate
would make the label ambiguous everywhere it is matched by name. The two
relabellings and the deactivations are checked against the exact text
``taxonomy/0006`` wrote, and a row somebody has since edited is left alone
rather than overruled.

**The reverse is honest about what it cannot know.** It restores version 1.0's
two labels, its descriptions and its sort orders, reactivates the twelve and
removes the five new rows **only where no Matter carries them** — a rollback may
not delete a classification somebody recorded, so a new type that is in use is
deactivated instead. What it cannot restore is an activation state that was
already false before this ran: every one of the twelve is active today, seeded
that way by ``taxonomy/0006`` and never since changed, so the reverse is exact
for the database this migrates — but a deployment that had deactivated one of
them by hand would get it back active. That is stated rather than claimed away.
"""

from django.db import migrations

#: The twelve withdrawn keys and the labels ``taxonomy/0006`` gave them.
#: Checked rather than assumed: a row somebody has renamed is somebody's
#: decision, and deactivating it on the strength of a key alone would overrule
#: them silently.
WITHDRAWN = {
    "eelnou": "Eelnõu",
    "el-teatis": "EL teatis",
    "konsultatsioon": "Konsultatsioon",
    "strateegia": "Strateegia",
    "arengukava": "Arengukava",
    "tegevuskava": "Tegevuskava",
    "visioon": "Visioon",
    "korraldus": "Korraldus",
    "kaskkiri": "Käskkiri",
    "ettepanek": "Ettepanek",
    "kusitlus": "Küsitlus",
    "muu": "Muu",
}

#: key -> (version-1.0 label, version-1.0 description, version-2.0 label,
#: version-2.0 description). Both directions are written out, so the reverse
#: restores what ``taxonomy/0006`` actually wrote rather than what a later
#: manifest says it wrote.
RELABELLED = {
    "direktiiv": (
        "Direktiiv",
        (
            "Euroopa Liidu direktiiv. Direktiiv on alati ELi akt, seega eraldi "
            "«EL direktiiv» liiki ei ole."
        ),
        "ELi direktiiv",
        "Euroopa Liidu direktiiv. Direktiiv on alati ELi akt.",
    ),
    "el-maarus": (
        "EL määrus",
        (
            "Euroopa Liidu määrus, sealhulgas Euroopa Komisjoni oma. Eraldi liik "
            "Määrusest: ELi määrus kehtib vahetult ja seda ei võeta üle."
        ),
        "ELi määrus",
        (
            "Euroopa Liidu määrus, sealhulgas Euroopa Komisjoni oma. Eraldi liik "
            "Määrusest: ELi määrus kehtib vahetult ja seda ei võeta üle."
        ),
    ),
}

#: key, label, description, sort order — the five new types.
NEW = [
    (
        "koja-ettepanek",
        "Koja ettepanek või pöördumine",
        (
            "Koja enda algatatud ettepanek või pöördumine. Euroopa Komisjoni või "
            "muu asutuse ettepanek ei ole see."
        ),
        40,
    ),
    (
        "strateegia-arengukava-tegevuskava",
        "Strateegia, arengukava või tegevuskava",
        (
            "Riigisisene strateegiline dokument: strateegia, arengukava või "
            "tegevuskava. Üks liik, sest menetlus on neil kõigil sama."
        ),
        50,
    ),
    (
        "muu-siseriiklik",
        "Muu siseriiklik",
        (
            "Mõni muu riigisisene akt või dokument. Vali ka «Õigusakti liik» ja "
            "kirjuta, millega on tegemist — sellest ei teki uut liiki."
        ),
        60,
    ),
    (
        "eli-konsultatsioon",
        "ELi konsultatsioon",
        (
            "Euroopa Liidu institutsiooni avalik konsultatsioon või arvamuse "
            "küsimine. Riigisisene konsultatsioon ei ole see."
        ),
        70,
    ),
    (
        "muu-eli-dokument",
        "Muu ELi dokument",
        (
            "Mõni muu Euroopa Liidu dokument — teatis, roheline raamat, "
            "ettepanek. Vali ka «Õigusakti liik» ja kirjuta, millega on tegemist."
        ),
        100,
    ),
]

#: The reviewed order, for the five reused version-1.0 rows, as
#: ``key -> (version-1.0 sort order, version-2.0 sort order)``. The twelve
#: withdrawn rows keep the numbers ``taxonomy/0006`` gave them: they are never
#: in the same ordered list as these, so moving them would be churn nobody
#: could see.
RENUMBERED = {
    "vtk": (30, 10),
    "seadus": (10, 20),
    "maarus": (20, 30),
    "direktiiv": (50, 80),
    "el-maarus": (60, 90),
}


def _same_text(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def adopt(apps, schema_editor):
    LegalInstrumentType = apps.get_model("taxonomy", "LegalInstrumentType")

    # Every conflict is checked before anything is written, so a refusal stops
    # the whole change rather than leaving half of it.
    existing = list(LegalInstrumentType.objects.all())
    by_key = {item.key: item for item in existing}

    for key, label, _description, _sort_order in NEW:
        held = by_key.get(key)
        if held is not None and not _same_text(held.label_et, label):
            raise RuntimeError(
                f"LegalInstrumentType {key!r} already exists as {held.label_et!r}, and the "
                f"reviewed vocabulary calls it {label!r}. Renaming it here would move every "
                "Matter classified under it. Resolve the two by hand, then re-run."
            )
        clash = next(
            (item for item in existing if item.key != key and _same_text(item.label_et, label)),
            None,
        )
        if clash is not None:
            raise RuntimeError(
                f"LegalInstrumentType {clash.key!r} is already named {clash.label_et!r}, which "
                f"the reviewed vocabulary uses for key {key!r}. Two types sharing a label make "
                "every label-based match ambiguous. Resolve the two by hand, then re-run."
            )

    for key, (old_label, old_description, new_label, new_description) in RELABELLED.items():
        LegalInstrumentType.objects.filter(
            key=key, label_et=old_label, description=old_description
        ).update(label_et=new_label, description=new_description)

    for key, label in WITHDRAWN.items():
        held = by_key.get(key)
        if held is None or not _same_text(held.label_et, label):
            # Renamed since review, or absent. Left exactly as it is — see the
            # module docstring.
            continue
        LegalInstrumentType.objects.filter(key=key, is_active=True).update(is_active=False)

    for key, (old_order, new_order) in RENUMBERED.items():
        LegalInstrumentType.objects.filter(key=key, sort_order=old_order).update(
            sort_order=new_order
        )

    for key, label, description, sort_order in NEW:
        if key in by_key:
            continue
        LegalInstrumentType.objects.create(
            key=key,
            label_et=label,
            description=description,
            sort_order=sort_order,
            is_active=True,
        )


def revert(apps, schema_editor):
    """Version 1.0's vocabulary again, without deleting anything in use."""
    LegalInstrumentType = apps.get_model("taxonomy", "LegalInstrumentType")

    for key, (old_label, old_description, new_label, new_description) in RELABELLED.items():
        LegalInstrumentType.objects.filter(
            key=key, label_et=new_label, description=new_description
        ).update(label_et=old_label, description=old_description)

    for key, (old_order, new_order) in RENUMBERED.items():
        LegalInstrumentType.objects.filter(key=key, sort_order=new_order).update(
            sort_order=old_order
        )

    LegalInstrumentType.objects.filter(key__in=list(WITHDRAWN)).update(is_active=True)

    for key, label, description, sort_order in NEW:
        item = LegalInstrumentType.objects.filter(key=key).first()
        if item is None:
            continue
        if item.matters.exists():
            # Deactivated rather than deleted. A rollback may not take a
            # classification a lawyer recorded with it; `MatterEditForm` keeps
            # a retired type readable and editable on the Matters carrying it.
            LegalInstrumentType.objects.filter(pk=item.pk).update(is_active=False)
            continue
        pristine = (
            _same_text(item.label_et, label)
            and _same_text(item.description, description)
            and item.sort_order == sort_order
        )
        if pristine:
            item.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("taxonomy", "0007_retire_coalition_and_transposition_areas"),
        # The reverse asks whether a Matter carries one of the new types, so the
        # join table has to exist by the time this runs in either direction.
        ("matters", "0015_matter_legal_instruments"),
    ]

    operations = [
        migrations.RunPython(adopt, revert),
    ]
