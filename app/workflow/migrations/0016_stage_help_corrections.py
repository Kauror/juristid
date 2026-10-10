"""Correct three `Hetkeseis` explanations the live QA of 9 October 2026 found wrong.

The texts are the department's own, transcribed by ``workflow/0006`` sentence for
sentence, and two of them were flagged there and in ADR 0032 as questions only the
product owner could answer. The owner has now answered them (the Juristid lawyer-UX
brief of 9 October 2026, F2 and F3):

* **``other`` («Muu»)** carried the ``eu_procedure`` («ELi menetluses») text word
  for word. ``workflow/0006`` shipped the duplication rather than guess which of
  the two was wrong; it was «Muu». It now says when «Muu» is the answer, in the
  owner's wording. «ELi menetluses» is not touched.
* **``in_force`` («Jõustunud»)** told the lawyer, for *every* EU act, to choose
  «jõustunud» only when Estonia does not have to change its own law — which for a
  regulation points at «ELi õiguse ülevõtmise ootel», a stage the approved
  guidance matrix deliberately dims for ``el-maarus`` because a regulation is
  directly applicable and is not transposed (docs/adr/0130 §5). The rule is a
  directive's, and now says so; a regulation that has entered into force is in
  force, including when Estonian implementing measures are still pending, and
  including when it applies only from a later date. Where a distinct Estonian
  implementing process is actually being followed, it is its own linked Teema;
  and the text says that «Jõustunud» ends the Teema, so outstanding work is
  placed before it closes (the owner's answer of 2026-10-10).
* **``awaiting_transposition`` («ELi õiguse ülevõtmise ootel»)** began «ELi
  õigusakti jõustumisest» — any EU act. Transposition is a directive's (or another
  act that requires it); it now says so, and that a regulation is not transposed.

Nothing else moves: no key, label, sort order, matrix row or stored ``Matter.stage``,
and no historical record is re-read. The owner decided on 2026-10-10 that no stage is
added for «a regulation in force, Estonian implementing measures pending»: the
regulation's Teema is «Jõustunud», and a real Estonian implementing process is followed
as its own linked Teema (docs/adr/0130, amendment of 2026-10-09, «not colour alone»).

**Fails closed**, as ``workflow/0006`` did: a row whose text is no longer exactly the
one ``workflow/0006`` wrote has been edited by somebody, and is left as it is. The
reverse restores the previous text under the same rule.

Frozen copies, never imports, and ``workflow`` only — the rule ``workflow/0007``
states for data migrations in this app.
"""

from django.db import migrations

#: What `workflow/0006` wrote. Frozen: the guard has to compare against what that
#: migration actually stored.
_EU_PROCEDURE_0006 = (
    "Kasuta 2 juhul: 1) ELi dokumendi (EK avalik konsultatsioon, EK määruse "
    "ettepanek, EK direktiivi ettepanek, EK strateegia, muu ELi dokument) "
    "menetlus alates Eesti seisukoha Riigikogu ELi asjade komisjonis "
    "kinnitamisest kuni ELi dokumendi menetluse lõpuni (nt EK avalikule "
    "konsultatsioonile järgneb EK ettepanek; EK ettepanek jõustub; tuleb otsus, "
    "et ELi menetlusega ei minda edasi). 2) ELi dokument saadetakse meile "
    "arvamuse avaldamiseks ja Eesti ei koosta selle teema kohta seisukohti."
)

PREVIOUS = {
    "other": _EU_PROCEDURE_0006,
    "in_force": (
        "Jõustunud Riigi Teatajas või ELi aktide puhul EUR-Lexis; kui jõustub "
        "ELi akt, siis märgi hetkeseisuks „jõustunud“ üksnes juhul, kui Eesti ei "
        "pea ELi õiguse tulemusena siseriiklikku õigust muutma."
    ),
    "awaiting_transposition": (
        "ELi õigusakti jõustumisest kuni Eesti koostab ELi õiguse ülevõtmiseks "
        "VTK või eelnõu või muu dokumendi."
    ),
}

CORRECTED = {
    "other": "Vali „Muu“, kui ükski loetletud hetkeseis ei kirjelda teema tegelikku olukorda.",
    "in_force": (
        "Jõustunud Riigi Teatajas või ELi aktide puhul EUR-Lexis. Kui jõustub ELi "
        "direktiiv, märgi hetkeseisuks „jõustunud“ üksnes juhul, kui Eesti ei pea "
        "seda siseriiklikku õigusesse üle võtma; seni on hetkeseis „ELi õiguse "
        "ülevõtmise ootel“. ELi määrus kohaldub vahetult ja seda üle ei võeta: "
        "jõustunud määrus on „jõustunud“ ka siis, kui seda hakatakse kohaldama "
        "hiljem või kui Eesti rakendusmeetmed on veel pooleli. Kui Eestis on "
        "käimas eraldi rakendusakti menetlus, mida Koda jälgib, ava selle jaoks "
        "seotud teema. „Jõustunud“ lõpetab teema."
    ),
    "awaiting_transposition": (
        "ELi direktiivi (või muu ülevõtmist vajava ELi akti) jõustumisest kuni "
        "Eesti koostab selle ülevõtmiseks VTK, eelnõu või muu dokumendi. ELi "
        "määrust üle ei võeta — see kohaldub vahetult."
    ),
}


def correct(apps, schema_editor):
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")
    for key, text in CORRECTED.items():
        StageVocabulary.objects.filter(key=key, help_text=PREVIOUS[key]).update(help_text=text)


def restore(apps, schema_editor):
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")
    for key, text in CORRECTED.items():
        StageVocabulary.objects.filter(key=key, help_text=text).update(help_text=PREVIOUS[key])


class Migration(migrations.Migration):
    dependencies = [
        ("workflow", "0015_current_register_status_labels"),
    ]

    operations = [
        migrations.RunPython(correct, restore),
    ]
