"""Estonian labels shared by the Lovable sync administration and nightly review.

These describe the approved allowlist in ADR 0192, not arbitrary source columns.
Never add excluded source fields (e.g. markmed) to a review surface.
"""

LOVABLE_FIELD_LABELS = {
    "*": "Teema",
    "reference": "Teema tunnus",
    "title": "Pealkiri",
    "owner": "Vastutaja",
    "next_action_text": "Järgmine tegevus (sõnastus)",
    "next_action_date": "Järgmise tegevuse kuupäev",
    "onenote_link": "OneNote'i link",
    "stage": "Hetkeseis",
    "opinion_deadline": "Arvamuse tähtaeg",
    "sent": "Välja",
    "sent_date": "Välja saatmise kuupäev",
    "addressee": "Kellele",
    "koda_initiative": "Koja ettepanek",
    "legal_act": "Õigusakt",
    "received": "Sisse",
    "commencements": "Jõustumised",
    "operational_scope": "Operatiivne ulatus",
    "continuation": "Jätkuteema (menetlusetappide seos)",
    "new_topic": "Uus Lovable'i teema",
}

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
}

#: Lovable's own column names, as its audit ledger (`topic_change_field`) names
#: them, to the canonical field they feed (`lovable_sync.SOURCE_FIELDS`). A
#: column not listed here is shown by its own name only, never by its value.
LOVABLE_SOURCE_COLUMNS = {
    "teema": "title",
    "vastutaja": "owner",
    "uus_vastutaja": "owner",
    "jargmiseks": "next_action_text",
    "tegevuse_tahtaeg": "next_action_date",
    "onenote_link": "onenote_link",
    "hetkeseis": "stage",
    "arvamuse_tahtaeg": "opinion_deadline",
    "valja": "sent",
    "valja_kuupaev": "sent_date",
    "kellele": "addressee",
    "koja_ettepanek": "koda_initiative",
    "oigusakt": "legal_act",
    "sisse": "received",
    "operatiivne_ulatus": "operational_scope",
}

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

#: Fields only the wider observation reads (`lovable_observation.OBSERVE_FIELDS`)
#: and the ledger's composite names, as the observation pages label them.
LOVABLE_FIELD_LABELS.update(
    {
        "commencements_managed": "Jõustumisi hallatakse",
        "responsible_changes": "Vastutaja vahetused",
        "stage_links": "Menetlusetappide seosed",
        "topic_links": "Seotud teemad",
    }
)
LOVABLE_SOURCE_COLUMNS.update(
    {
        "nr": "reference",
        "joustumised": "commencements",
        "joustumised_hallatud": "commencements_managed",
        "menetlusetapid": "stage_links",
        "seosed": "topic_links",
    }
)
