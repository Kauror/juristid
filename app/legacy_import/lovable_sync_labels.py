"""Estonian labels shared by the Lovable sync administration and nightly review.

These describe the approved allowlist in ADR 0192, not arbitrary source columns.
Never add excluded source fields (e.g. markmed) to a review surface.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LovableField:
    """One field as every Lovable page, export, importer check and MCP tool
    names it: its Estonian label and the Lovable columns behind it. A
    description only: the engine's extractors (`lovable_sync.SOURCE_FIELDS`),
    its policy (`FIELD_POLICY`) and its rules read their own code."""

    #: The canonical field name (`LovableSyncDecision.field`).
    name: str
    #: The Estonian label every surface shows.
    label: str
    #: Lovable's own column names behind it, as its audit ledger
    #: (`topic_change_field`) names them, in reading order.
    columns: tuple[str, ...] = ()
    #: Whether a version-2 package may hold its columns for review
    #: (`lovable_pilot.HELD_FIELD_COLUMNS`). `operatiivne_ulatus` may not: the
    #: scope is compared, but no import decision ever holds it.
    import_holdable: bool = False
    #: Read only by the wider observation (`lovable_observation.OBSERVE_FIELDS`)
    #: or the ledger, never synchronised.
    observed_only: bool = False


#: Every field, in the order the administration pages list them.
LOVABLE_FIELDS: tuple[LovableField, ...] = (
    LovableField("*", "Teema"),
    LovableField("reference", "Teema tunnus", ("nr",)),
    LovableField("title", "Pealkiri", ("teema",), import_holdable=True),
    LovableField("owner", "Vastutaja", ("vastutaja", "uus_vastutaja"), import_holdable=True),
    LovableField("former_owner", "Endine vastutaja"),
    LovableField(
        "next_action_text", "Järgmine tegevus (sõnastus)", ("jargmiseks",), import_holdable=True
    ),
    LovableField(
        "next_action_date",
        "Järgmise tegevuse kuupäev",
        ("tegevuse_tahtaeg",),
        import_holdable=True,
    ),
    LovableField("onenote_link", "OneNote'i link", ("onenote_link",), import_holdable=True),
    LovableField("stage", "Hetkeseis", ("hetkeseis",), import_holdable=True),
    LovableField(
        "opinion_deadline", "Arvamuse tähtaeg", ("arvamuse_tahtaeg",), import_holdable=True
    ),
    LovableField("sent", "Välja", ("valja",), import_holdable=True),
    LovableField("sent_date", "Välja saatmise kuupäev", ("valja_kuupaev",), import_holdable=True),
    LovableField("addressee", "Kellele", ("kellele",), import_holdable=True),
    LovableField("koda_initiative", "Koja ettepanek", ("koja_ettepanek",), import_holdable=True),
    LovableField("legal_act", "Õigusakt", ("oigusakt",), import_holdable=True),
    LovableField("received", "Sisse", ("sisse",), import_holdable=True),
    LovableField("commencements", "Jõustumised", ("joustumised",), import_holdable=True),
    LovableField("operational_scope", "Operatiivne ulatus", ("operatiivne_ulatus",)),
    LovableField("continuation", "Jätkuteema (menetlusetappide seos)"),
    LovableField("new_topic", "Uus Lovable'i teema"),
    LovableField(
        "commencements_managed",
        "Jõustumisi hallatakse",
        ("joustumised_hallatud",),
        observed_only=True,
    ),
    LovableField("responsible_changes", "Vastutaja vahetused", observed_only=True),
    LovableField("stage_links", "Menetlusetappide seosed", ("menetlusetapid",), observed_only=True),
    LovableField("topic_links", "Seotud teemad", ("seosed",), observed_only=True),
)

#: A field's description by name.
LOVABLE_FIELD = {spec.name: spec for spec in LOVABLE_FIELDS}


def lovable_columns(*field_names: str) -> tuple[str, ...]:
    """The Lovable columns behind ``field_names``, in turn."""
    return tuple(column for name in field_names for column in LOVABLE_FIELD[name].columns)


#: Field → Estonian label, for every surface.
LOVABLE_FIELD_LABELS = {spec.name: spec.label for spec in LOVABLE_FIELDS}

LOVABLE_REASONS = {
    "HELD_FIELD": "Seda välja ei uuendata automaatselt.",
    "MATTER_CLOSED": "Teema on Juristides suletud.",
    "NO_BASELINE": "Viimati rakendatud väärtus pole teada.",
    "UNRESOLVED_OWNER": "Vastutajat ei leitud üheselt.",
    "OWNER_NOT_ASSIGNABLE": "Sellele inimesele ei saa praegu tööd määrata.",
    "TITLE_WAS_REVIEWED": "Pealkiri vaadati impordil üle ja erineb allikast.",
    "TITLE_EMPTY": "Allika pealkiri on tühi.",
    "NEXT_STEP_TEXT_CHANGED": (
        "Järgmise tegevuse sõnastus muutus; liigi (tee/oota/jälgi) otsustab inimene."
    ),
    "DATE_WITH_TEXT_CHANGE": "Kuupäev muutus koos sõnastusega.",
    "NO_SOURCE_STEP": "Juristides pole sellest allikast tulnud tegevust.",
    "STEP_NOT_FROM_SOURCE_WORDING": "Juristide tegevus ei põhine allika sõnastusel.",
    "DATE_CLEARED": "Allikas kustutati kuupäev.",
    "DATE_IN_PAST": "Uus kuupäev on möödas; see muutuks kohe hilinenuks.",
    "STEP_DATE_NOT_EXACT": "Tegevuse kuupäev pole täpne päev.",
    "LINK_NOT_HTTPS": "Link pole https-aadress.",
    "VERIFY_STOPPED": "Jäi kirjutamata: varasema teema tagasilugemine ebaõnnestus ja öö peatati.",
    "VERIFY_MISMATCH": "Kirjutati, kuid tagasilugemine ei vastanud kirjutatule.",
    "IMPORT_HELD": "Imporditud ülevaatamiseks: impordil jäeti see väli inimese otsustada.",
    # The conditional fields and the continuation (docs/adr/0213).
    "STAGE_ENDS_WORK": "Uus hetkeseis lõpetaks töö ja sulgeks teema; selle otsustab inimene.",
    "REQUEST_ALREADY_OPEN": (
        "Teemal on juba praegune arvamuse küsimine; selle muutmise või asendamise otsustab inimene."
    ),
    "REQUEST_CLEARED": "Allikas eemaldati arvamuse tähtaeg; praegust küsimist ei lõpetata ise.",
    "REQUEST_SENT_IN_SOURCE": (
        "Allikas on «välja» või saatmise kuupäev (ka «ei saatnud»): uut küsimist ei looda."
    ),
    "REQUEST_ON_INITIATIVE": "Koja algatusel pole uut arvamuse küsimist.",
    "COMMENCEMENT_DAY_CLASH": "Juristides on samal päeval juba teine jõustumise kirje.",
    "CONTINUATION_CHANGED": (
        "Lovable'i menetlusetappide seos muutus; jätku kirjutamine sulgeks teema, nii et "
        "selle otsustab inimene."
    ),
    # New topics (docs/adr/0213).
    "UUID_HELD_BY_TOMBSTONE": (
        "Selle Lovable'i tunnusega teema on Juristides kustutatud; seda ei looda uuesti."
    ),
    "UUID_HELD_BY_UNSYNCED_TEEMA": (
        "Selle Lovable'i tunnust hoiab teema, mis pole sünkroonimises; seose otsustab inimene."
    ),
    "REFERENCE_TAKEN": "Teema number on Juristides juba kasutusel.",
    "REFERENCE_OF_DELETED_TEEMA": "Teema numbrit kasutas jäädavalt kustutatud teema.",
    "REFERENCE_RELEASED_BY_REPLACEMENT": (
        "Teema numbri vabastas allikapõhine asendus; selle uuesti andmise otsustab inimene."
    ),
    "NOT_IN_READING": "Teema ei olnud selle öö lugemises tervikuna olemas.",
    "READING_REFUSED": "Teemat ei saa ohutult lugeda (nt vastutajal pole kontot).",
    "READING_INVALID": "Loetud teema ei vasta impordi reeglitele.",
    "PLAN_PROBLEM": "Impordiplaan leidis takistuse (vastutaja, etapp, õigusakt või kokkupõrge).",
    "NO_TARGET_COHORT": "Pole kohorti, millega uus teema liita.",
    "CREATE_FAILED": "Teema loomine ebaõnnestus; midagi ei jäänud pooleli.",
    "NEW_TOPIC_BREAKER": "Ühe öö uusi teemasid oli üle piiri; ühtegi ei loodud.",
    "RESOLVED": "Inimene otsustas; masin seda ei loo.",
    # Source authority's technical holds (docs/adr/0213 §10): nothing else waits.
    "SUCCESSOR_NOT_IN_JURISTID": (
        "Lovable'i järgi jätkub teema teise teemana, mida Juristides veel pole; teema "
        "suletakse jätkuna, kui järglane on olemas."
    ),
    "SUCCESSOR_AMBIGUOUS": "Lovable'i menetlusetappide seos nimetab mitu järglast või iseenda.",
    "SUCCESSOR_DEFERRED_BY_BREAKER": (
        "Järglane loodi täna, kuid selle teema sulgemine oleks ületanud öise piirangu; "
        "järgmine käivitus viib selle lõpuni."
    ),
    "CONTINUATION_CYCLE": (
        "Lovable'i menetlusetappide seosed viivad ringiga tagasi samale teemale; "
        "teema elukäiku ei muudetud."
    ),
    "STAGE_UNMAPPED": "Lovable'i hetkeseisu silti ei saa Juristide hetkeseisuks kaardistada.",
    "LIFECYCLE_HELD": "Ootab teema sulgemist (järglane puudub Juristides).",
    "CLOSURE_HOLD_BREAKER": (
        "Ühe öö uusi ootel sulgemisi (Juristides pooleli töö) oli üle piiri; "
        "öö ei kirjutanud midagi."
    ),
    "CLOSURE_HELD_AT_WRITE": (
        "Kirjutamise hetkel ilmus Juristides pooleli töö ja sulgemine jäi ootele; selle teema "
        "muud muudatused ootavad järgmist ööd."
    ),
    "LOCAL_WORK_AT_CLOSURE": (
        "Lovable sulges teema, kuid Juristides on pooleli töö (planeeritud tegevus, "
        "järelkontroll, kodulehe ülevaade või kaasamise ootus); sulgemine ootab, midagi "
        "ei tühistatud."
    ),
    "RESTORED_SOURCE_VALUE": "Lovable'i väärtus taastati; Juristides oli seda muudetud.",
    "ANSWERED_IN_JURISTID_NOT_IN_LOVABLE": (
        "Arvamuse küsimine on Juristides vastatud või lõpetatud, kuid Lovable pole saatmist "
        "veel kirja pannud; uut küsimist ei looda."
    ),
    "RESTORE_BREAKER": (
        "Ühe öö taastamisi oli üle piiri: Lovable'i muudatused kirjutati, kohalike muudatuste "
        "taastamised ootavad."
    ),
    "HISTORY_OWNER_UNRESOLVED": (
        "Ajaloolise teema vastutaja nimi ei vasta ühelegi Juristide kasutajale "
        "(endine töötaja); nimi jääb Lovable'i allikasse."
    ),
    "HISTORY_REOPENED_OUTSIDE_SCOPE": (
        "Lovable avas ajaloolise teema uuesti, kuid see pole Lovable'i operatiivses "
        "ulatuses; Juristides ei muudetud midagi."
    ),
    "CLOSURE_REASON_UNKNOWN": (
        "Lovable'i järgi on teema lõpetatud, kuid põhjust pole kirjas; põhjust ei "
        "mõelda välja, nii et avatud teemat ei suleta."
    ),
    "OUTCOME_HELD": (
        "Arvamuse saatmise seis on ootel (vastuoluline või võimatu), nii et arvamuse "
        "küsimist ei muudeta."
    ),
    "STEP_DATE_WITHOUT_TEXT": "Lovable'is on tegevuse kuupäev, aga pole tegevuse sõnastust.",
    "DATE_UNREADABLE": "Lovable'i kuupäeva ei saa kuupäevana lugeda.",
    "SENT_CONTRADICTORY": (
        "Lovable'i «välja» ja saatmise kuupäev ütlevad eri asja (nt «ei saatnud» ja kuupäev)."
    ),
    "SENT_VALUE_UNREADABLE": (
        "Lovable'i «välja» pole kuupäev ega «ei saatnud»; saatmist ei märgita arvates."
    ),
    "SENT_DATE_IN_FUTURE": "Lovable'i saatmise kuupäev on tulevikus.",
    "ADDRESSEE_WITHOUT_SEND": (
        "Adressaat on märgitud, kuid saatmist pole; seda pole kuhugi kirjutada."
    ),
    "TRACK_NOT_STATED": (
        "Lovable'is pole see enam Koja algatus, kuid ei ütle, milline menetlusliik see on."
    ),
    "INSTRUMENT_UNMAPPED": "Õigusakti silt ei vasta ühelegi kehtivale liigile.",
    "INSTRUMENT_SEVERAL": "Õigusakti silt nimetab mitu liiki; teemal saab olla üks.",
    "INSTRUMENT_CLEARED": "Lovable'is eemaldati õigusakt; Juristide liigitust ei kustutata.",
    "RECEIVED_IN_FUTURE": "Saabumise kuupäev on tulevikus.",
    "RECEIVED_ON_INITIATIVE": "Koja algatusel pole saabumise kuupäeva.",
    "SCOPE_NOT_REPRESENTABLE": (
        "Juristides pole operatiivse ulatuse mõistet; ulatusest väljumine ei sulge teemat."
    ),
    "COMMENCEMENT_CHANGED_OR_REMOVED": (
        "Lovable'is muudeti või eemaldati jõustumine; Juristide kirjet ei tühistata ise."
    ),
    "COMMENCEMENT_UNREADABLE": "Jõustumise liiki või päeva ei saa üheselt lugeda.",
    "LINK_REMOVED_KEPT": "Lovable'is eemaldati OneNote'i link; allikaviiteid ei kustutata.",
}

#: Lovable's own column names, as its audit ledger (`topic_change_field`) names
#: them, to the canonical field they feed (`lovable_sync.SOURCE_FIELDS`). A
#: column not listed here is shown by its own name only, never by its value.
#: A lookup: only each field's own columns keep their order (`columns`); the
#: order across fields is not significant.
LOVABLE_SOURCE_COLUMNS = {column: spec.name for spec in LOVABLE_FIELDS for column in spec.columns}

#: Lovable audit-ledger actions, in the page's words (any case).
LOVABLE_SOURCE_ACTIONS = {
    "muutmine": "Muudetud",
    "lisamine": "Loodud",
    "kustutamine": "Kustutatud",
    "taastamine": "Taastatud",
    "insert": "Loodud",
    "create": "Loodud",
    "update": "Muudetud",
    "delete": "Kustutatud",
    "restore": "Taastatud",
}

#: What a wider reading saw happen to a source topic (`ObservedChangeKind`).
LOVABLE_OBSERVED_KINDS = {
    "NEW": "Uus teema",
    "CHANGED": "Muutunud",
    "MISSING": "Kadus lugemisest",
    "BACK": "Taas lugemises",
    "RENUMBERED": "Number muutus",
    #: A Teema whose cohort was enrolled after the previous night (docs/adr/0202
    #: §6): it enters the comparison; nothing changed in Lovable.
    "ENROLLED": "Lisandus võrdlusesse",
}

#: A synchronised topic's source activity, as Lovable's dashboard counts
#: «ongoing» (docs/adr/0202 §6). Counts only.
LOVABLE_ACTIVITY_LABELS = {
    "ONGOING": "Käimas",
    "FINISHED": "Lõpetatud (jõustunud või rohkem ei tegele)",
    "CONTINUES": "Jätkub teise teema all",
    "NOT_OPERATIONAL": "Väljaspool operatiivset ulatust",
    "MISSING": "Lugemises puudu",
}

#: The synchronised cohorts, in the pages' words (docs/adr/0202 §6).
LOVABLE_COHORT_LABELS = {
    "lovable-pilot": "Piloot",
    "lovable-operational": "Operatiivne register",
    "lovable-register": "Lovable'i register",
    "lovable-history": "Lovable'i ajalugu",
}

#: The readiness simulation's categories: what the *existing* rules would do
#: with the newest observation if APPLY were on. Never an activation.
LOVABLE_SIMULATION_LABELS = {
    "SAFE_UPDATE_CANDIDATE": "Ohutu uuenduse kandidaat",
    "LOCAL_KEPT": "Juristide muudatus jääb alles",
    "CONFLICT": "Vastuolu",
    "MANUAL_REVIEW": "Vajab inimese otsust",
    "MISSING_BASELINE": "Viimati rakendatud väärtus pole teada",
    "UNSUPPORTED_FIELD": "Välja ei uuendata automaatselt",
    "UNKNOWN_SOURCE": "Allikateema puudub või number muutus",
    "CONVERGED": "Juba kooskõlas",
    "UNCHANGED": "Muutusteta",
}
