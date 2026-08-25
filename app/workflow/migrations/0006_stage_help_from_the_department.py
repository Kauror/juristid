"""Replace the seeded Hetkeseis explanations with the department's own.

``workflow/0004`` seeded a one-line gloss per stage, written while the
vocabulary was being read out of the workbook and marked ``is_provisional``
precisely because the wording was still the department head's call. It has now
been made: the texts below are the business descriptions supplied with the
approved Uus teema design on 2026-08-25, transcribed sentence for sentence.

They are longer than what they replace, and that is the point. "Eelnõu on
valitsuse menetluses" restates the label; the supplied text says *from* which
event *until* which event a file is in that stage, which is the question a
lawyer choosing between two adjacent stages actually has. They are what the
tooltip on Uus teema renders.

**Transcribed, not edited.** Two things a reader will notice, both left alone
deliberately and both flagged for the product owner rather than fixed here:

* ``idea`` carries an unbalanced parenthesis in the source. Closing it would be
  this migration guessing where the sentence was meant to end.
* ``other`` carries the same text as ``eu_procedure``. That is what the source
  says. Two stages explaining themselves identically is very likely a slip in
  the supplied document, but which of the two is wrong is not something a
  migration may decide — so the duplication ships visibly and is named in the
  implementation report and in ADR 0032.

**One raw workbook value has no stage here.** ``rohkem pole tegevusi plaanis``
was supplied with a description alongside these, and it is not a Hetkeseis:
``workflow/0004`` reads it as the ``MONITORING_STOPPED`` disposition, because it
says Koda stopped working on the file rather than where the external process
stands. Its text belongs to the closure control and is recorded in ADR 0032
until that control is given one. Nothing here invents a stage for it.

**Fails closed.** A stage whose help text is no longer the one ``workflow/0004``
wrote has been edited by somebody, and this leaves it exactly as it is. The
reverse restores the seeded text under the same rule, so rolling back does not
overwrite the department's wording either.
"""

from django.db import migrations

#: What ``workflow/0004`` seeded. Frozen copy: the reverse has to restore what
#: that migration actually wrote, not whatever the manifest says later.
SEEDED = {
    "idea": "Algatus on teada, kuid ametlikku menetlust ei ole veel alanud.",
    "consultation": "Eelnõu on kooskõlastusringil ja arvamuse esitamise aeg on jooksmas.",
    "government": "Eelnõu on valitsuse menetluses.",
    "parliament": "Eelnõu on Riigikogu menetluses.",
    "awaiting_entry": "Akt on vastu võetud, kuid ei ole veel jõustunud.",
    "in_force": (
        "Akt on jõustunud. See ei tähenda, et Koja töö on lõppenud — "
        "rakendamise jälgimine on tavaline töö ja teema sulgemine on eraldi otsus."
    ),
    "estonian_eu_position": "Kujundatakse Eesti seisukohta ELi algatuse kohta.",
    "eu_procedure": "Algatus on Euroopa Liidu institutsioonide menetluses.",
    "awaiting_transposition": "ELi akt on vastu võetud ja oodatakse riigisisest ülevõtmist.",
    "other": "Menetlus ei sobi ühegi ülaltoodud etapi alla.",
}

#: The department's own wording, supplied 2026-08-25.
_EU_PROCEDURE = (
    "Kasuta 2 juhul: 1) ELi dokumendi (EK avalik konsultatsioon, EK määruse "
    "ettepanek, EK direktiivi ettepanek, EK strateegia, muu ELi dokument) "
    "menetlus alates Eesti seisukoha Riigikogu ELi asjade komisjonis "
    "kinnitamisest kuni ELi dokumendi menetluse lõpuni (nt EK avalikule "
    "konsultatsioonile järgneb EK ettepanek; EK ettepanek jõustub; tuleb otsus, "
    "et ELi menetlusega ei minda edasi). 2) ELi dokument saadetakse meile "
    "arvamuse avaldamiseks ja Eesti ei koosta selle teema kohta seisukohti."
)

SUPPLIED = {
    "idea": (
        "Siseriiklikule eelnõule eelnev etapp (nt VTK kooskõlastusringile "
        "saatmisest, meie ettepaneku või pöördumise esitamisest, siseriiklikule "
        "küsitlusele vastamisest kuni idee jõudmiseni järgmise hetkeseisu "
        "etapini (nt VTK-le järgneb eelnõu; saame vastuse, et ettepanekut ei "
        "viida ellu ja otsustame, et me rohkem ei tegele selle teemaga edasi; "
        "VTK-le ei järgne eelnõud)."
    ),
    "consultation": (
        "Seaduse või määruse eelnõu kooskõlastusringile saatmisest kuni eelnõu "
        "saatmiseni valitsusele või otsuseni, et eelnõuga ei minda edasi."
    ),
    "government": (
        "Seaduse või määruse eelnõu esitamisest valitsusele kuni valitsuse "
        "heakskiitmiseni või otsuseni, et eelnõuga ei minda edasi."
    ),
    "parliament": (
        "Riigikogu menetlusse võtmisest kuni seaduse vastuvõtmiseni või "
        "otsuseni, et eelnõud ei võeta vastu."
    ),
    "awaiting_entry": (
        "Riigikogu on seaduse või valitsus/minister määruse vastu võtnud või EL "
        "on määruse või direktiivi heaks kiitnud, kuid akt pole veel jõustunud."
    ),
    "in_force": (
        "Jõustunud Riigi Teatajas või ELi aktide puhul EUR-Lexis; kui jõustub "
        "ELi akt, siis märgi hetkeseisuks „jõustunud“ üksnes juhul, kui Eesti ei "
        "pea ELi õiguse tulemusena siseriiklikku õigust muutma."
    ),
    "estonian_eu_position": (
        "ELi teema (nt EK avalik konsultatsioon, EK määruse ettepanek, EK "
        "direktiivi ettepanek, EK strateegia, muu ELi dokument) meile arvamuse "
        "avaldamiseks saatmisest kuni Eesti seisukohtade kinnitamiseni Riigikogu "
        "ELi asjade komisjonis või otsuseni, et Eesti seisukohti ei koostata."
    ),
    "eu_procedure": _EU_PROCEDURE,
    "awaiting_transposition": (
        "ELi õigusakti jõustumisest kuni Eesti koostab ELi õiguse ülevõtmiseks "
        "VTK või eelnõu või muu dokumendi."
    ),
    # Verbatim duplicate of `eu_procedure` — see the module docstring. Written
    # as the same constant rather than copied, so that nobody "tidies" one of
    # the two and hides the fact that the source says the same thing twice.
    "other": _EU_PROCEDURE,
}


def adopt(apps, schema_editor):
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")
    for key, text in SUPPLIED.items():
        StageVocabulary.objects.filter(key=key, help_text=SEEDED[key]).update(help_text=text)


def revert(apps, schema_editor):
    StageVocabulary = apps.get_model("workflow", "StageVocabulary")
    for key, text in SUPPLIED.items():
        StageVocabulary.objects.filter(key=key, help_text=text).update(help_text=SEEDED[key])


class Migration(migrations.Migration):
    dependencies = [
        ("workflow", "0005_deadline_requires_a_date"),
    ]

    operations = [
        migrations.RunPython(adopt, revert),
    ]
