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
}
