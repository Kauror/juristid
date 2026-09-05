"""The rule vocabularies, in one readable place.

Everything the analyser recognises is written down here as data: which words
suggest which Valdkond and how much they count, which phrases make a date a
response deadline and which make it a decoy, what marks a legislative title,
which cues name a Menetlusliik. A maintainer should be able to answer «miks see
seda pakub?» by reading this file, without reading the code that applies it.

Three properties every entry keeps:

* **Closed lists, matched on word boundaries.** No stemmer, no similarity, no
  model. An Estonian stem is matched by writing the stem and letting the
  ending vary — ``käibemaks`` finds *käibemaksu*, *käibemaksuga* — which is a
  spelled-out prefix, not fuzziness (docs/adr/0026).
* **Weights say how specific a word is.** *Käibemaksuseadus* can only mean one
  area; *maks* is in half the documents Koda receives. A generic word scores
  very little, a legal term scores a lot, and the thresholds in
  `analysis.py` are set so that one generic word never carries a suggestion.
* **Keys are stable identifiers, never database primary keys.** Policy areas
  are addressed by ``PolicyArea.key`` and tracks by ``Track`` values. A key
  that no longer exists in the active vocabulary is reported as a diagnostic
  and ignored, never mapped onto something else (`resolvers.py`).

The vocabulary is version-stamped. A change that alters what the same text
produces bumps ``RULES_VERSION``, so an evaluation run can say which rules it
measured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.workflow.enums import Track

RULES_VERSION = "1.0"


@dataclass(frozen=True)
class Signal:
    """One thing to look for, and how much finding it is worth."""

    #: A regular expression over casefolded text. Diacritics are kept: *õigus*
    #: and *oigus* are different words here (docs/adr/0026).
    pattern: str
    weight: int
    #: What the panel says this signal was — the words a person can look for.
    label: str = ""

    @property
    def regex(self) -> re.Pattern[str]:
        return _compile(self.pattern)


_COMPILED: dict[str, re.Pattern[str]] = {}


def _compile(pattern: str) -> re.Pattern[str]:
    found = _COMPILED.get(pattern)
    if found is None:
        found = re.compile(pattern, re.IGNORECASE)
        _COMPILED[pattern] = found
    return found


#: How many times one signal may count. A word repeated on every page of a
#: draft is one piece of evidence, not forty.
SIGNAL_HIT_CAP = 3


# ---------------------------------------------------------------------------
# Valdkonnad
# ---------------------------------------------------------------------------
#
# Keyed by the current PolicyArea.key values (app/taxonomy/reference_data.py,
# vocabulary version 3.0). The descriptions in that manifest say what each
# area is meant to hold; the signals below are the words a document uses when
# it is about that thing.
#
# Weights: 5 — a law's name or a term that belongs to one area only; 3–4 — a
# specific legal term; 2 — a domain word; 1 — a generic word that only
# supports a stronger one.

AREA_RULES: dict[str, tuple[Signal, ...]] = {
    "maksejouetus": (
        Signal(r"\bmaksejõuetus", 5, "maksejõuetus"),
        Signal(r"\bpankrot", 4, "pankrot"),
        Signal(r"\bsaneerimi", 4, "saneerimine"),
        Signal(r"\bvõlgade ümberkujundami", 4, "võlgade ümberkujundamine"),
        Signal(r"\bpankrotihaldur", 3, "pankrotihaldur"),
        Signal(r"\blikvideerimi", 1, "likvideerimine"),
    ),
    "raamatupidamine": (
        Signal(r"\braamatupidamise seadus", 5, "raamatupidamise seadus"),
        Signal(r"\braamatupidami", 4, "raamatupidamine"),
        Signal(r"\bmajandusaasta aruan", 4, "majandusaasta aruanne"),
        Signal(r"\bkestlikkusaruan", 3, "kestlikkusaruandlus"),
        Signal(r"\baudiit", 3, "audit"),
        Signal(r"\bfinantsaruan", 3, "finantsaruandlus"),
        Signal(r"\baruandlus", 1, "aruandlus"),
    ),
    "intellektuaalomand": (
        Signal(r"\bintellektuaalomand", 5, "intellektuaalomand"),
        Signal(r"\bautoriõigus", 4, "autoriõigus"),
        Signal(r"\bkaubamärk", 4, "kaubamärk"),
        Signal(r"\bärisaladus", 4, "ärisaladus"),
        Signal(r"\btööstusomand", 4, "tööstusomand"),
        Signal(r"\bpatent", 3, "patent"),
        Signal(r"\blitsents", 2, "litsents"),
    ),
    "toetusmeetmed": (
        Signal(r"\briigiabi", 5, "riigiabi"),
        Signal(r"\btoetusmee[td]", 4, "toetusmeede"),
        Signal(r"\bstruktuuri(?:fond|toetus)", 3, "struktuuritoetus"),
        Signal(r"\btaasterahastu", 3, "taasterahastu"),
        Signal(r"\binvesteeringutoetus", 3, "investeeringutoetus"),
        Signal(r"\brahastamisvahend", 2, "rahastamisvahend"),
        Signal(r"\btoetus", 1, "toetus"),
    ),
    "koalitsioonilepped": (
        Signal(r"\bkoalitsioonilep", 5, "koalitsioonilepe"),
        Signal(r"\bvalitsuse tegevusprogramm", 4, "valitsuse tegevusprogramm"),
        Signal(r"\btegevusprogramm", 2, "tegevusprogramm"),
    ),
    "oigusloome": (
        Signal(r"\bhea õigusloome", 5, "hea õigusloome"),
        Signal(r"\bkaasamise hea tava", 5, "kaasamise hea tava"),
        Signal(r"\bnormitehni", 3, "normitehnika"),
        Signal(r"\bõigusloome", 2, "õigusloome"),
        Signal(r"\bmõju(?:de)? ?analüüs", 2, "mõjuanalüüs"),
        Signal(r"\bhalduskoormus", 2, "halduskoormus"),
    ),
    "energeetika": (
        Signal(r"\belektrituruseadus", 5, "elektrituruseadus"),
        Signal(r"\benergiamajanduse korralduse seadus", 5, "energiamajanduse korralduse seadus"),
        Signal(r"\belektritur", 4, "elektriturg"),
        Signal(r"\bvõrgutasu", 4, "võrgutasu"),
        Signal(r"\btaastuvenerg", 4, "taastuvenergia"),
        Signal(r"\bvarustuskindlus", 3, "varustuskindlus"),
        Signal(r"\benergiamajandus", 3, "energiamajandus"),
        Signal(r"\bkaugkü[tl]", 3, "kaugküte"),
        Signal(r"\b(?:tuule|päikese)par[gk]", 3, "tuule- või päikesepark"),
        Signal(r"\bmaagaas", 2, "maagaas"),
        Signal(r"\belektri", 1, "elekter"),
        Signal(r"\benergia", 1, "energia"),
        Signal(r"\bkütus", 1, "kütus"),
    ),
    "riigihanked": (
        Signal(r"\briigihangete seadus", 5, "riigihangete seadus"),
        Signal(r"\briigihan[gk]", 5, "riigihange"),
        Signal(r"\bhankemenetlus", 4, "hankemenetlus"),
        Signal(r"\bvaidlustuskomisjon", 4, "vaidlustuskomisjon"),
        Signal(r"\bhankeleping", 3, "hankeleping"),
        Signal(r"\bhankija", 3, "hankija"),
        Signal(r"\bpakkuja", 2, "pakkuja"),
    ),
    "haridus": (
        Signal(r"\bkutseharidus", 4, "kutseharidus"),
        Signal(r"\bkõrgharidus", 4, "kõrgharidus"),
        Signal(r"\bharidussüsteem", 4, "haridussüsteem"),
        Signal(r"\bkutseõp", 4, "kutseõpe"),
        Signal(r"\btäiskasvanuharidus", 4, "täiskasvanuharidus"),
        Signal(r"\bõppekava", 3, "õppekava"),
        Signal(r"\bülikool", 2, "ülikool"),
        Signal(r"\bharidus", 1, "haridus"),
        Signal(r"\boskus", 1, "oskused"),
    ),
    "tarbijakaitse": (
        Signal(r"\btarbijakaitse", 5, "tarbijakaitse"),
        Signal(r"\bebaaus\w* kaubandustava", 4, "ebaausad kaubandustavad"),
        Signal(r"\btarbija", 3, "tarbija"),
        Signal(r"\bmüügitingimus", 2, "müügitingimused"),
        Signal(r"\bgarantii", 2, "garantii"),
        Signal(r"\be-kaubandus", 2, "e-kaubandus"),
    ),
    "alkohol-tubakas": (
        Signal(r"\balkoholiseadus", 5, "alkoholiseadus"),
        Signal(r"\btubakaseadus", 5, "tubakaseadus"),
        Signal(r"\balkohol", 4, "alkohol"),
        Signal(r"\btubak", 4, "tubakas"),
        Signal(r"\b(?:e-sigaret|nikotiin)", 4, "e-sigaret või nikotiin"),
    ),
    "digiteemad": (
        Signal(r"\bküberturvalisuse seadus", 5, "küberturvalisuse seadus"),
        Signal(r"\bküberturv", 4, "küberturvalisus"),
        Signal(r"\btehis(?:intellekt|aru)", 4, "tehisintellekt"),
        Signal(r"\bandmekaitse", 3, "andmekaitse"),
        Signal(r"\bisikuandme", 3, "isikuandmed"),
        Signal(r"\bdigiteenus", 3, "digiteenus"),
        Signal(r"\be-teenus", 3, "e-teenus"),
        Signal(r"\binfoühiskonna teenus", 3, "infoühiskonna teenus"),
        Signal(r"\binfosüsteem", 1, "infosüsteem"),
        Signal(r"\bdigi", 1, "digi-"),
        Signal(r"\bplatvorm", 1, "platvorm"),
    ),
    "finantsoigus-rahapesu": (
        Signal(r"\brahapesu", 5, "rahapesu"),
        Signal(r"\bterrorismi rahastami", 5, "terrorismi rahastamine"),
        Signal(r"\bkrediidiasutus", 4, "krediidiasutus"),
        Signal(r"\bmakseteenus", 4, "makseteenus"),
        Signal(r"\bfinantsteenus", 3, "finantsteenus"),
        Signal(r"\binvesteerimisfond", 3, "investeerimisfond"),
        Signal(r"\bfinantsinspektsioon", 3, "Finantsinspektsioon"),
        Signal(r"\bkrüptovara", 3, "krüptovara"),
        Signal(r"\bväärtpaber", 3, "väärtpaber"),
        Signal(r"\btarbijakrediit", 2, "tarbijakrediit"),
        Signal(r"\bkindlustus", 2, "kindlustus"),
        Signal(r"\bkrediid", 1, "krediit"),
    ),
    "ehitus": (
        Signal(r"\behitusseadustik", 5, "ehitusseadustik"),
        Signal(r"\bplaneerimisseadus", 5, "planeerimisseadus"),
        Signal(r"\behitusl[ou]", 4, "ehitusluba"),
        Signal(r"\b(?:detail|üld)planeering", 4, "planeering"),
        Signal(r"\behitusprojekt", 3, "ehitusprojekt"),
        Signal(r"\bplaneering", 2, "planeering"),
        Signal(r"\behiti", 2, "ehitis"),
        Signal(r"\behitus", 2, "ehitus"),
        Signal(r"\bkinnis(?:vara|asja)", 2, "kinnisvara"),
    ),
    "arioigus": (
        Signal(r"\bäriseadustik", 5, "äriseadustik"),
        Signal(r"\bvõlaõigusseadus", 5, "võlaõigusseadus"),
        Signal(r"\btsiviilseadustik", 4, "tsiviilseadustiku üldosa seadus"),
        Signal(r"\büh[ie]nguõigus", 4, "ühinguõigus"),
        Signal(r"\bäriõigus", 4, "äriõigus"),
        Signal(r"\bäriühing", 3, "äriühing"),
        Signal(r"\bosaühing", 3, "osaühing"),
        Signal(r"\baktsiaselts", 3, "aktsiaselts"),
        Signal(r"\bäriregist", 3, "äriregister"),
        Signal(r"\bosanik", 2, "osanik"),
        Signal(r"\btsiviilkohtumenetlus", 2, "tsiviilkohtumenetlus"),
        Signal(r"\bleping", 1, "leping"),
    ),
    "valistoojoud": (
        Signal(r"\bvälismaalaste seadus", 5, "välismaalaste seadus"),
        Signal(r"\bvälistööjõu", 5, "välistööjõud"),
        Signal(r"\bvälismaalas", 4, "välismaalane"),
        Signal(r"\belamisl[ou]", 4, "elamisluba"),
        Signal(r"\blühiajali\w* töötami", 4, "lühiajaline töötamine"),
        Signal(r"\brändepoliitik", 4, "rändepoliitika"),
        Signal(r"\bsisserände piirarv", 3, "sisserände piirarv"),
        Signal(r"\bviisa", 2, "viisa"),
    ),
    "maksud-ja-toll": (
        Signal(r"\bkäibemaksuseadus", 5, "käibemaksuseadus"),
        Signal(r"\btulumaksuseadus", 5, "tulumaksuseadus"),
        Signal(r"\btolliseadus", 5, "tolliseadus"),
        Signal(r"\bmaksukorraldus", 5, "maksukorraldus"),
        Signal(r"\bkäibemaks", 5, "käibemaks"),
        Signal(r"\btulumaks", 5, "tulumaks"),
        Signal(r"\bsotsiaalmaks", 4, "sotsiaalmaks"),
        Signal(r"\baktsiis", 4, "aktsiis"),
        Signal(r"\bmaksuhaldur", 4, "maksuhaldur"),
        Signal(r"\bmaksu- ja tolliamet", 3, "Maksu- ja Tolliamet"),
        Signal(r"\bmaksustami", 3, "maksustamine"),
        Signal(r"\bmaksukohustus", 3, "maksukohustus"),
        Signal(r"\btoll", 3, "toll"),
        Signal(r"\bmaksud", 2, "maksud"),
        Signal(r"\bmaksumäär", 3, "maksumäär"),
        Signal(r"\bdeklaratsioon", 1, "deklaratsioon"),
    ),
    "toosuhted-tookeskkond": (
        Signal(r"\btöölepingu seadus", 5, "töölepingu seadus"),
        Signal(r"\btööleping", 5, "tööleping"),
        Signal(r"\btööaeg|\btööaja", 4, "tööaeg"),
        Signal(r"\btöötasu", 4, "töötasu"),
        Signal(r"\btöötervishoi", 4, "töötervishoid"),
        Signal(r"\btööohutus", 4, "tööohutus"),
        Signal(r"\btöökeskkon", 4, "töökeskkond"),
        Signal(r"\bkollektiivleping", 4, "kollektiivleping"),
        Signal(r"\btöövaidlus", 4, "töövaidlus"),
        Signal(r"\btöösuh", 4, "töösuhe"),
        Signal(r"\bpuhkus", 3, "puhkus"),
        Signal(r"\blähetus", 3, "lähetus"),
        Signal(r"\btöötuskindlustus", 3, "töötuskindlustus"),
        Signal(r"\btöötaja", 1, "töötaja"),
        Signal(r"\btööandja", 1, "tööandja"),
        Signal(r"\bpalk|\bpalga", 1, "palk"),
    ),
    "keskkond": (
        Signal(r"\bkeskkonnaseadustik", 5, "keskkonnaseadustik"),
        Signal(r"\bjäätmeseadus", 5, "jäätmeseadus"),
        Signal(r"\bpakendiseadus", 5, "pakendiseadus"),
        Signal(r"\bkeskkonnatasu", 5, "keskkonnatasu"),
        Signal(r"\bkeskkonnamõju", 4, "keskkonnamõju"),
        Signal(r"\bkeskkonnal[ou]", 4, "keskkonnaluba"),
        Signal(r"\bjäätme", 4, "jäätmed"),
        Signal(r"\b(?:heitkogus|kasvuhoonegaas)", 4, "heitkogused"),
        Signal(r"\bpakend", 3, "pakend"),
        Signal(r"\bringmajandus", 3, "ringmajandus"),
        Signal(r"\bkliima", 3, "kliima"),
        Signal(r"\breostus", 3, "reostus"),
        Signal(r"\blooduskaitse", 3, "looduskaitse"),
        Signal(r"\bsüsinik", 2, "süsinik"),
        Signal(r"\bkeskkon", 2, "keskkond"),
        Signal(r"\bvee", 1, "vesi"),
    ),
    "eli-oiguse-ulevotmine": (
        Signal(r"\bülevõtmi", 5, "ülevõtmine"),
        Signal(r"\bülereguleeri", 4, "ülereguleerimine"),
        Signal(r"\büle võ[te]", 3, "üle võtta"),
        Signal(r"\bharmoneeri", 3, "harmoneerimine"),
        Signal(r"\bdirektiiv", 3, "direktiiv"),
        Signal(r"\(el\)\s*\d{4}/\d+", 2, "ELi õigusakti number"),
    ),
    "arengukavad-strateegiad": (
        Signal(r"\barengustrateegi", 5, "arengustrateegia"),
        Signal(r"\barengukava", 5, "arengukava"),
        Signal(r"\bstrateegia", 4, "strateegia"),
        Signal(r"\beesti 2035", 4, "Eesti 2035"),
        Signal(r"\btegevuskava", 2, "tegevuskava"),
        Signal(r"\bpikaajali", 1, "pikaajaline"),
        Signal(r"\bvisioon", 1, "visioon"),
    ),
}

#: Below this a policy area is not offered at all: one generic word is noise.
AREA_MEDIUM_THRESHOLD = 4
#: From this a policy area may be pre-checked on an empty form.
AREA_HIGH_THRESHOLD = 8
#: How many areas the panel offers. Three useful candidates beat a list of
#: everything that scored once (brief §10).
AREA_LIMIT = 3


# ---------------------------------------------------------------------------
# Menetlusliik
# ---------------------------------------------------------------------------
#
# Keyed by Track values. `requires` names signal labels that must *all* be
# present before the track may reach HIGH: transposition needs both the act
# of transposing and a directive to transpose, or it is somebody's remark
# about EU law in an otherwise domestic draft.


@dataclass(frozen=True)
class TrackRule:
    signals: tuple[Signal, ...]
    requires: tuple[str, ...] = ()
    #: The highest confidence this rule may reach on its own. Koda's own
    #: initiative is never inferred confidently from an incoming document.
    ceiling: str = "HIGH"


TRACK_RULES: dict[str, TrackRule] = {
    Track.EU_INITIATIVE: TrackRule(
        signals=(
            Signal(r"\bcom\s*\(\s*\d{4}\s*\)\s*\d+", 5, "COM-viide"),
            Signal(
                r"\beuroopa komisjoni? (?:ettepanek|algatus|teatis|konsultatsioon)",
                4,
                "Euroopa Komisjoni ettepanek",
            ),
            Signal(r"\b(?:eli|el-i|euroopa liidu) algatus", 4, "ELi algatus"),
            Signal(
                r"\beuroopa parlamendi ja nõukogu (?:määrus|direktiiv)\w*\s+(?:eelnõu|ettepanek)",
                4,
                "EP ja nõukogu õigusakti ettepanek",
            ),
            Signal(
                r"\bproposal for a (?:regulation|directive)",
                4,
                "proposal for a regulation/directive",
            ),
            Signal(r"\bseisukohtade kujundami", 2, "seisukohtade kujundamine"),
            Signal(r"\beesti seisukoh", 2, "Eesti seisukohad"),
            Signal(r"\beuropean commission", 2, "European Commission"),
            Signal(r"\bavalik\w* konsultatsioon", 1, "avalik konsultatsioon"),
        ),
    ),
    Track.NATIONAL_TRANSPOSITION: TrackRule(
        signals=(
            Signal(r"\bülevõtmi", 4, "ülevõtmine"),
            Signal(r"\büle võ[te]", 3, "ülevõtmine"),
            Signal(r"\bharmoneeri", 2, "ülevõtmine"),
            Signal(r"\bdirektiiv", 3, "direktiiv"),
            Signal(r"\b\d{4}/\d{1,4}/(?:el|eü)\b", 2, "direktiiv"),
            Signal(r"\btranspon", 2, "ülevõtmine"),
        ),
        requires=("ülevõtmine", "direktiiv"),
    ),
    Track.STRATEGY: TrackRule(
        signals=(
            Signal(r"\barengukava", 4, "arengukava"),
            Signal(r"\barengustrateegi", 4, "arengustrateegia"),
            Signal(r"\bstrateegia", 4, "strateegia"),
            Signal(r"\bpikaajali\w* (?:kava|vaade|sihid)", 2, "pikaajaline kava"),
            Signal(r"\btegevuskava", 1, "tegevuskava"),
            Signal(r"\bvisioon", 1, "visioon"),
        ),
    ),
    Track.IMPLEMENTATION: TrackRule(
        signals=(
            Signal(r"\brakendus(?:juhend|juhis)|\bjuhendmaterjal", 4, "rakendusjuhend"),
            Signal(r"\bjärelevalve", 3, "järelevalve"),
            Signal(r"\brakendami\w*\s+(?:juhis|juhend|kord)", 3, "rakendamise juhis"),
            Signal(r"\brakendusakt", 2, "rakendusakt"),
            Signal(r"\bjuhend\b|\bjuhis\b", 2, "juhend"),
        ),
    ),
    Track.KODA_INITIATIVE: TrackRule(
        signals=(
            Signal(r"\bkoja (?:algatus|ettepanek|pöördumine)", 4, "Koja algatus"),
            Signal(r"\bkaubandus-tööstuskoja (?:algatus|ettepanek|pöördumine)", 4, "Koja algatus"),
            Signal(r"\bomaalgatus", 3, "omaalgatus"),
        ),
        ceiling="MEDIUM",
    ),
    Track.DOMESTIC: TrackRule(
        signals=(
            Signal(r"\bseaduse (?:muutmise )?(?:seaduse )?eelnõu", 4, "seaduse eelnõu"),
            Signal(r"\bväljatöötamiskavatsus|\bvtk\b", 4, "väljatöötamiskavatsus"),
            Signal(r"\bmääruse eelnõu", 3, "määruse eelnõu"),
            Signal(r"\bkooskõlastamiseks|\bkooskõlastusring", 2, "kooskõlastamine"),
            Signal(r"\beelnõude infosüsteem", 2, "eelnõude infosüsteem"),
            Signal(r"\bseletuskir", 1, "seletuskiri"),
            Signal(r"\briigikogu", 1, "Riigikogu"),
            Signal(r"\bvabariigi valitsus", 1, "Vabariigi Valitsus"),
            Signal(r"\bministeerium", 1, "ministeerium"),
        ),
    ),
}

#: EU signals of this strength make DOMESTIC unavailable: a national draft
#: that transposes a directive is NATIONAL_TRANSPOSITION, not both.
TRACK_EU_SUPPRESSES_DOMESTIC_AT = 3
TRACK_MEDIUM_THRESHOLD = 3
TRACK_HIGH_THRESHOLD = 5
#: The lead the strongest track needs over the runner-up to be HIGH alone.
TRACK_HIGH_MARGIN = 2


# ---------------------------------------------------------------------------
# Arvamuse tähtaeg
# ---------------------------------------------------------------------------
#
# A date is not a deadline. The words around it are what make it one, and the
# words around it are also what make it a decoy: the letter's own date, the
# day an act enters into force, the date of the letter being answered.

#: Words that, in the same clause as a date, say *by this day* about a
#: response. One of these is enough for HIGH when nothing contradicts it.
DEADLINE_STRONG_CUES: tuple[Signal, ...] = (
    Signal(r"\bhiljemalt\b", 3, "hiljemalt"),
    Signal(r"\btähtae[gj]", 3, "tähtaeg"),
    Signal(r"\btähtpäev", 3, "tähtpäev"),
    Signal(r"\bkuupäevaks\b", 3, "kuupäevaks"),
    Signal(r"\bkuni\b", 2, "kuni"),
)

#: Verbs and nouns of asking: *palume esitada*, *ootame ettepanekuid*.
DEADLINE_REQUEST_CUES: tuple[Signal, ...] = (
    Signal(r"\bpalu(?:me|n|takse)\b", 2, "palume"),
    Signal(r"\boota(?:me|b|takse)\b", 2, "ootame"),
    Signal(r"\boodatud\b", 1, "oodatud"),
    Signal(r"\bsoovi(?:me|b)\b", 1, "soovime"),
)

#: What is being asked for. Weak alone; with a request verb they make MEDIUM.
DEADLINE_RESPONSE_CUES: tuple[Signal, ...] = (
    Signal(r"\barvamus", 2, "arvamus"),
    Signal(r"\bseisukoh", 2, "seisukoht"),
    Signal(r"\bettepanek", 2, "ettepanek"),
    Signal(r"\btagasisid", 2, "tagasiside"),
    Signal(r"\bkooskõlast", 2, "kooskõlastus"),
    Signal(r"\bkommentaar", 1, "kommentaar"),
    Signal(r"\bmärkus", 1, "märkus"),
    Signal(r"\bvasta", 1, "vastus"),
    Signal(r"\besita", 1, "esitada"),
    Signal(r"\bedasta", 1, "edastada"),
    Signal(r"\bteata", 1, "teatada"),
)

#: Clauses a date belongs to instead. Any of these in the same clause turns
#: the date into a decoy, whatever else the clause says.
DEADLINE_DECOY_CUES: tuple[Signal, ...] = (
    Signal(r"\bjõustu", 0, "jõustumine"),
    Signal(r"\bkehtib alates|\bkehtima\b|\bkehtestat", 0, "kehtestamine"),
    Signal(r"\ballkirjasta", 0, "allkirjastamine"),
    Signal(r"\bkoostat|\bkoostas\b", 0, "koostamine"),
    Signal(r"\bvastu võet|\bvastu võtt", 0, "vastuvõtmine"),
    Signal(r"\bkinnitat", 0, "kinnitamine"),
    Signal(r"\bregistreerit", 0, "registreerimine"),
    Signal(r"\btoimu[sn]", 0, "toimumine"),
    Signal(r"\bsaadet", 0, "saatmine"),
    Signal(r"\bavaldat", 0, "avaldamine"),
    # «Teie 04.09.2026 nr …» and «Meie 05.09.2026 nr …» are the two letters'
    # own dates. The bare pronoun is not a decoy: «ootame Teie seisukohta
    # 18. septembriks» is a request.
    Signal(r"\bteie\s+(?:\d{1,2}\.\d{1,2}\.\d{2,4}|kirja\w*|nr\b)", 0, "Teie kiri"),
    Signal(r"\bmeie\s+(?:\d{1,2}\.\d{1,2}\.\d{2,4}|kirja\w*|nr\b)", 0, "Meie kiri"),
    Signal(r"\bdokumendi kuupäev", 0, "dokumendi kuupäev"),
    Signal(r"\bkuupäev\s*:", 0, "kuupäev"),
    Signal(r"\bkirja(?:ga|le)?\s+nr", 0, "kirja nr"),
    Signal(r"\bvastuseks\b", 0, "vastuseks"),
    Signal(r"\bseisuga\b", 0, "seisuga"),
    Signal(r"\bmuudet", 0, "muutmine"),
    Signal(r"\bredaktsioon", 0, "redaktsioon"),
)

#: A date within this many characters of a document's start with no cue at
#: all is its dateline, not a deadline.
DATELINE_WINDOW = 200

#: How much of the clause on either side of a date is read for cues.
DEADLINE_CONTEXT_BEFORE = 200
DEADLINE_CONTEXT_AFTER = 120

#: «kolme nädala jooksul» — a deadline the letter states without a day. Shown
#: as a finding, never computed into a date: the day it counts from is not
#: something the analyser knows.
RELATIVE_DEADLINE = re.compile(
    r"\b(?P<count>\d{1,3}|ühe|kahe|kolme|nelja|viie|kuue|seitsme|kaheksa|üheksa|kümne|"
    r"neljateist(?:kümne)?|kolmekümne)\s+(?P<unit>tööpäeva|päeva|nädala|kuu)\s+jooksul\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pealkiri
# ---------------------------------------------------------------------------

#: Language a legislative document's own heading carries. One of these on a
#: heading-shaped line near the start makes it a strong title candidate.
TITLE_STRONG_CUES: tuple[Signal, ...] = (
    Signal(r"\bseaduse (?:muutmise )?(?:seaduse )?eelnõu", 5, "seaduse eelnõu"),
    Signal(r"\bmääruse eelnõu", 5, "määruse eelnõu"),
    Signal(r"\bväljatöötamiskavatsus", 5, "väljatöötamiskavatsus"),
    Signal(r"\bvtk\b", 4, "VTK"),
    Signal(r"\barengukava", 4, "arengukava"),
    Signal(r"\bstrateegia", 4, "strateegia"),
    Signal(r"\beelnõu", 3, "eelnõu"),
    Signal(r"\bseadus", 2, "seadus"),
    Signal(r"\bmäärus", 2, "määrus"),
    Signal(r"\bdirektiiv", 2, "direktiiv"),
    Signal(r"\bettepanek", 1, "ettepanek"),
    Signal(r"\bkonsultatsioon", 1, "konsultatsioon"),
)

#: Lines that are a document type, not a title. «Seletuskiri» is what the
#: file is, not what the Matter is about.
TITLE_TYPE_ONLY = re.compile(
    r"^\s*(?:seletuskiri|kaaskiri|kooskõlastuskiri|eelnõu|lisa\s*\d*|memo|protokoll|"
    r"kiri|teatis|pöördumine)\s*[.:]?\s*$",
    re.IGNORECASE,
)

#: «Seletuskiri … eelnõu juurde» — the title is what sits between.
TITLE_MEMORANDUM_WRAPPER = re.compile(
    r"^\s*seletuskiri\s+(?P<title>.+?)\s+juurde\s*[.:]?\s*$", re.IGNORECASE
)

#: Mail prefixes stripped from a subject, repeatedly. Estonian clients write
#: VS: and SV:, English ones Re: and Fwd:.
SUBJECT_PREFIXES = re.compile(
    r"^\s*(?:(?:re|fw|fwd|vs|sv|edasi|vastus|aw|wg)\s*:\s*)+", re.IGNORECASE
)

#: A heading candidate is a line this long at most; anything longer is prose.
TITLE_MAX_LENGTH = 220
TITLE_MIN_LENGTH = 12
#: How far into a document a heading may sit to count as its formal title.
TITLE_OPENING_WINDOW = 2500

#: Lines that address somebody rather than name the subject.
TITLE_SALUTATION = re.compile(r"^\s*(?:lp\.?|lugupeetud|austatud|tere)\b", re.IGNORECASE)

#: Verbs of sending and asking, and the connectives of a sentence. A line
#: carrying one is prose that mentions an act, not the act's heading.
TITLE_PROSE_CUES = re.compile(
    r"\b(?:saadame|edastame|esitame|teatame|palume|palun|ootame|soovime|lisame|"
    r"anname teada|mis on|on registreeritud|registreeritud|seoses|vastavalt|käesolevaga|"
    r"kohta|muudetakse|täpsustatakse|sätestatakse|kehtestatakse|tunnistatakse|"
    r"nähakse ette|on koostatud|on saadetud)\b",
    re.IGNORECASE,
)
TITLE_PROSE_PENALTY = 8

#: What a covering letter appends to a draft's name to say why it was sent.
#: Removed from the end of a heading and from nowhere else.
TITLE_PURPOSE_SUFFIX = re.compile(
    r"[\s—–-]+(?:kooskõlastamiseks|arvamuse (?:avaldamiseks|andmiseks|esitamiseks)|"
    r"seisukoha (?:kujundamiseks|andmiseks)|tutvumiseks|teadmiseks|kommenteerimiseks|"
    r"ettepanekute esitamiseks)\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

#: An e-mail address, with every part bounded.
#:
#: The domain is written as *labels separated by dots* rather than as one
#: character class containing the dot: the second form lets the engine split a
#: long run like `a.a.a.a…` in many ways when no top-level domain follows, and
#: a document with such a run costs time quadratic in its length. The bounds
#: are the real ones — 63 characters per DNS label, 64 for the local part.
EMAIL_ADDRESS = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@(?:[A-Za-z0-9\-]{1,63}\.){1,8}[A-Za-z]{2,24}")

#: Two capitalised words, letters only, Estonian letters included. A person's
#: name in a signature block; deliberately not a general NER.
PERSON_NAME = re.compile(
    r"(?<![\w-])(?P<name>[A-ZÕÄÖÜŠŽ][a-zõäöüšž]{1,}(?:-[A-ZÕÄÖÜŠŽ][a-zõäöüšž]{1,})?"
    r"[ \t]+[A-ZÕÄÖÜŠŽ][a-zõäöüšž]{1,}(?:-[A-ZÕÄÖÜŠŽ][a-zõäöüšž]{1,})?)(?![\w-])"
)

#: Labels that introduce a contact person in a letter.
CONTACT_LABELS = re.compile(
    r"\b(?:kontaktisik|kontakt|koostaja|koostas|koostanud|saatja|lisainfo|lisateave|"
    r"täiendav\w* (?:info|teave)|e-post|e-mail|email|tel(?:efon)?|"
    r"küsimus\w*\s+korral|pöördu\w*|kontakteeru\w*|vastutav\w*)\b\s*[:.]?",
    re.IGNORECASE,
)

#: What closes a letter, and therefore opens its signature block.
SIGNATURE_OPENERS = re.compile(
    r"\b(?:lugupidamisega|parimate soovidega|austusega|tervitades|heade soovidega|"
    r"kind regards|best regards)\b",
    re.IGNORECASE,
)

#: Words that look like a two-word name and are not one.
NOT_A_NAME = frozenset(
    {
        "lugupidamisega",
        "parimate",
        "soovidega",
        "eesti",
        "vabariik",
        "kaubandus",
        "tööstuskoda",
        "ministeerium",
        "euroopa",
        "komisjon",
        "riigikogu",
        "vabariigi",
        "valitsus",
        "seaduse",
        "eelnõu",
        "arvamus",
        "teie",
        "meie",
        "lisa",
        "lugupeetud",
        "austatud",
        "tere",
        "kontakt",
        "kontaktisik",
        "koostaja",
        "tallinn",
        "tartu",
    }
)

#: How far from the end of a document its signature block may start.
SIGNATURE_WINDOW = 900
CONTACT_CONTEXT_BEFORE = 220


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

#: A document number: digits and letters joined by at least one separator.
#: «1-4/26/1234-2», «2-1/26-01234», «7.1-4/2026/1234». Never a bare number
#: and never a date — dates are excluded by the caller.
REFERENCE_NUMBER = r"(?P<ref>\d[0-9A-Za-z]*(?:[./-][0-9A-Za-z]+)+)"

#: «Meie 05.09.2026 nr 1-4/26/1234» / «Teie 04.09.2026 nr 7-1/26/321».
MEIE_TEIE_REFERENCE = re.compile(
    r"\b(?P<who>meie|teie)\b\s*[:.]?\s*(?:(?P<date>\d{1,2}\.\d{1,2}\.\d{4})\.?\s*)?"
    r"(?:nr\.?|number|kiri)?\s*[:.]?\s*" + REFERENCE_NUMBER,
    re.IGNORECASE,
)

#: «dokumendi nr 1-4/26/1234», «kirja nr …», «reg. nr …», «viide: …».
LABELLED_REFERENCE = re.compile(
    r"\b(?P<label>dokumendi nr|kirja nr|reg\.?\s*nr|registreerimisnumber|viide|viitenumber|"
    r"toimiku? nr|toimik|nr)\b\.?\s*[:.]?\s*" + REFERENCE_NUMBER,
    re.IGNORECASE,
)

#: The words that may stand between an EIS label and the number it introduces:
#: «EIS toimik nr», «EISis registreeritud numbriga». A closed list of stems,
#: each with a bounded Estonian ending.
_EIS_FILLER = (
    r"(?:toimik|dokumend|dokument|number|numbri|nr\.?|viide|viite|viita|"
    r"registreeri|kood|kande)\w{0,6}"
)

#: An explicitly EIS-labelled reference. The grammar of an EIS number is not
#: asserted — the repository holds no verified example of one — so what is
#: captured is «the token after an EIS label», and a date-shaped token is
#: refused by the caller (brief §11).
#:
#: **Every repetition here is bounded, and that is load-bearing.** The filler
#: was once an unbounded `(?:…\w*\s*)*`, whose alternatives could each swallow
#: the same run of letters in exponentially many ways: a document containing
#: `numbnumbnumb…` and no digits made the engine explore every split and the
#: request never returned. Document text is not ours to trust for shape, so the
#: filler is a bounded number of bounded words (`tests/test_assisted_intake.py`
#: holds the whole scan inside a time budget).
EIS_REFERENCE = re.compile(
    r"\b(?:EIS(?:-?i[a-z]{0,3})?|eelnõude infosüsteem\w{0,8})\b\s*"
    r"(?:" + _EIS_FILLER + r"\s+){0,4}"
    r"[:.]?\s*" + REFERENCE_NUMBER,
    re.IGNORECASE,
)

#: The public EIS host, the only one the specification names
#: (docs/master-specification.md §31.2).
EIS_URL = re.compile(r"https?://(?:www\.)?eelnoud\.valitsus\.ee/[^\s<>()\"']+", re.IGNORECASE)

ANY_URL = re.compile(r"https?://[^\s<>()\"']+")

#: A European Commission document reference.
COM_REFERENCE = re.compile(
    r"\bCOM\s*\(\s*(?P<year>\d{4})\s*\)\s*(?P<number>\d{1,4})(?:\s*final)?\b"
)

#: An EU act number in either order: «(EL) 2024/1234», «2019/1024/EL».
EU_ACT_REFERENCE = re.compile(
    r"(?:\((?:EL|EÜ|EU|EC)\)\s*(?:nr\s*)?\d{4}/\d{1,4}\b|\b\d{4}/\d{1,4}/(?:EL|EÜ|EU|EC)\b)"
)

#: How many links a document may contribute before the rest is noise.
URL_LIMIT = 5
REFERENCE_LIMIT = 8

REFERENCE_LABELS: dict[str, str] = {
    "meie_nr": "Saatja dokumendi nr",
    "teie_nr": "Viide Koja kirjale (Teie nr)",
    "document_nr": "Dokumendi nr",
    "eis": "EIS",
    "eis_url": "EIS link",
    "com": "ELi viide (COM)",
    "eu_act": "ELi õigusakti viide",
    "riigikogu": "Riigikogu menetlus",
    "url": "Link dokumendis",
}


# ---------------------------------------------------------------------------
# Organisations
# ---------------------------------------------------------------------------

#: Estonian case endings a name may carry in running text. The name itself
#: must match in full; only the ending may vary — «Kliimaministeeriumile» is
#: still Kliimaministeerium, «Kliimaamet» is not (docs/adr/0029).
ORGANISATION_SUFFIX = r"(?:i?(?:le|lt|st|ga|ks|ni|na|sse|s|l|d|de|t)?)"

#: An alias this short is an abbreviation whatever its recorded type, and an
#: abbreviation seen in a body is never strong evidence of a sender.
ABBREVIATION_LENGTH = 5

#: The opening block of a letter: letterhead and the address block, at most
#: this long, and never past the line that opens the body.
LETTERHEAD_WINDOW = 900

#: The line on which a letter stops being a header and starts talking: a
#: salutation, or the first sentence of sending or asking.
LETTER_BODY_OPENS = re.compile(
    r"^[ \t]*(?:lp\.?|lugupeetud|austatud|tere|saadame|edastame|esitame|palume|käesolevaga)\b",
    re.IGNORECASE | re.MULTILINE,
)

#: «Saatja:», «From:» — the one context that names a sender outright.
ORGANISATION_SENDER_CUES = re.compile(r"\b(?:saatja|from|koostaja)\s*:", re.IGNORECASE)

#: The number of separate organisations a body may name before mentions stop
#: being listed individually.
ORGANISATION_MENTION_LIMIT = 6
