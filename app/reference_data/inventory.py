"""What each reference vocabulary is, who may change it here, and what depends on it.

The B1 inventory of docs/adr/0187, written down once as data so the Haldus page
and the decision record cannot drift apart. Every row answers the same six
questions: where it is editable today, whether it is seeded or reviewed, what
references it, whether a change owes a search rebuild, what reporting reads it,
and what may never happen to it.

``editable`` is the only column that drives behaviour: it decides whether the
page offers a form or the «muudatus vajab arendaja kaudu ülevaadatud muudatust»
sentence instead.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from django.db.models import Count, QuerySet

#: The sentence a controlled vocabulary shows instead of a form.
REVIEWED_CHANGE_ONLY = (
    "Muudatus vajab arendaja kaudu ülevaadatud muudatust: see sõnastik tuleb "
    "koodist ja migratsioonist ning sellest sõltuvad aruandlus ja ajalugu."
)


@dataclass(frozen=True)
class Vocabulary:
    key: str
    label: str
    model: str
    editable_here: str
    source: str
    referenced_by: str
    reindex: str
    reporting: str
    delete_policy: str
    #: ``None`` when the vocabulary is managed on a page of its own.
    rows: Callable[[], QuerySet] | None = None
    url_name: str = ""


def _policy_areas() -> QuerySet:
    from app.taxonomy.models import PolicyArea

    return PolicyArea.objects.annotate(usage=Count("matters", distinct=True)).order_by(
        "sort_order", "name_et"
    )


def _legal_instruments() -> QuerySet:
    from app.taxonomy.models import LegalInstrumentType

    return LegalInstrumentType.objects.annotate(usage=Count("matters", distinct=True)).order_by(
        "sort_order", "label_et"
    )


def _stages() -> QuerySet:
    from app.workflow.models import StageVocabulary

    return StageVocabulary.objects.annotate(usage=Count("matters", distinct=True)).order_by(
        "sort_order", "label_et"
    )


VOCABULARIES: tuple[Vocabulary, ...] = (
    Vocabulary(
        key="organisatsioonid",
        label="Organisatsioonid",
        model="organisations.Organisation, OrganisationAlias",
        editable_here="Lisamine, nime parandamine, nimekujud (ADR 0187).",
        source=(
            "Avalik alusandmestik (ministeeriumid, Riigikogu ja komisjonid) kinnitatud "
            "plaani kaudu; ülejäänud lisatakse saatja/adressaadi väljadelt."
        ),
        referenced_by=(
            "Teema saatjad ja adressaat, sissekanded, arvamuste adressaadid, "
            "eelkäija/järglase seosed."
        ),
        reindex="Jah: nime ja nimekuju muutus jätab otsingule ümberehitamise võla.",
        reporting="Statistika asutuste lõiked ja eksport.",
        delete_policy=(
            "Ei kustutata ega liideta siin; alusandmestiku nime ja kinnitatud "
            "nimekujusid ei muudeta siin."
        ),
        url_name="reference_data:organisations",
    ),
    Vocabulary(
        key="sildid",
        label="Sildid",
        model="taxonomy.Tag, TagAlias",
        editable_here=(
            "Lisamine, nimi ja tähendus, nimekujud, liitmine ja aegunuks märkimine (ADR 0187)."
        ),
        source="Osakonna hallatav sõnastik (meistri spetsifikatsioon 14.7).",
        referenced_by="Teemade sildid ja arvamuste märksõnad.",
        reindex=(
            "Jah: nime ja nimekuju muutus jätab ümberehitamise võla, liitmine "
            "uuendab puudutatud teemad kohe."
        ),
        reporting="Statistika sildifilter.",
        delete_policy=(
            "Ei kustutata; liidetud või aegunud silt jääb kirjetele ja otsitavaks "
            "kanoonilise sildi kaudu."
        ),
        url_name="reference_data:tags",
    ),
    Vocabulary(
        key="valdkonnad",
        label="Valdkonnad",
        model="taxonomy.PolicyArea",
        editable_here="Ei — ainult vaatamine.",
        source="Kinnitatud alusandmestik migratsiooniga taxonomy/0002 (ADR 0029).",
        referenced_by="Teemade valdkonnad.",
        reindex="Jah: nimetamine ja kustutamine jätavad ümberehitamise võla.",
        reporting="Statistika peamine lõige «Teemad valdkonniti».",
        delete_policy="Ei kustutata; aegunud valdkond jääb ajaloolistele teemadele.",
        rows=_policy_areas,
    ),
    Vocabulary(
        key="oigusaktid",
        label="Õigusakti liigid",
        model="taxonomy.LegalInstrumentType",
        editable_here="Ei — ainult vaatamine.",
        source="Registri veerust ÕIGUSAKT üle vaadatud sõnastik (ADR 0070).",
        referenced_by="Teemade õigusakti liigid.",
        reindex="Ei.",
        reporting="Statistika ja ekspordi õigusakti veerg.",
        delete_policy="Ei kustutata; kasutusest väljas liik jääb ajaloolistele teemadele.",
        rows=_legal_instruments,
    ),
    Vocabulary(
        key="etapid",
        label="Hetkeseisud",
        model="workflow.StageVocabulary, LegacyStatusMapping",
        editable_here="Ei — ainult vaatamine.",
        source="Juristidega üle vaadatud etapisõnastik, seemendatakse käsuga.",
        referenced_by=(
            "Teema hetkeseis, hetkeseisu perioodid, ajalooliste siltide vasted, menetluse kulg."
        ),
        reindex="Ei.",
        reporting="Statistika hetkeseisu lõiked, osakonna töövaade.",
        delete_policy="Ei kustutata; viidatud etapp on kaitstud (PROTECT).",
        rows=_stages,
    ),
)

BY_KEY: dict[str, Vocabulary] = {vocabulary.key: vocabulary for vocabulary in VOCABULARIES}

#: The controlled vocabularies, shown read-only.
CONTROLLED: tuple[str, ...] = tuple(v.key for v in VOCABULARIES if v.rows is not None)
