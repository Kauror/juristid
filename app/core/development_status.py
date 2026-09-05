"""What the v2 design rebuild could not settle on its own.

One list, in one file, in version control. Not a database table and not a
project-management feature: these rows are decisions and deviations, they are
edited in a pull request beside the code they describe, and their history is the
repository's history.

Every row exists because implementing the approved design ran into one of the
things the brief says must never be solved by improvising product UI — a
contradiction between two parts of the handoff, a design requirement that would
have weakened authorization or invented data, a screen that was not actually
designed, or a deliberate temporary deviation.

Nothing here names a Matter, a member, a company or a person. The surface that
renders it is `/haldus/arendus/` and it is read by the department head and the
administrator, not by lawyers (`app/core/views.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The state a row is in. Deliberately three words, not a workflow.
OPEN = "Lahtine"
DECIDED = "Otsustatud"
DEVIATION = "Teadlik kõrvalekalle"


@dataclass(frozen=True)
class StatusItem:
    """One unresolved question, decision or deliberate deviation."""

    key: str
    area: str
    issue: str
    why: str
    state: str
    next_step: str
    #: The branch or pull request the row belongs to, when there is one.
    reference: str = ""
    #: Where in the handoff or the repository the reader should look.
    sources: tuple[str, ...] = field(default_factory=tuple)


#: Read top to bottom. Ordered by area rather than by severity: this is a
#: worklist for whoever is holding the redesign, not a risk register.
ITEMS: tuple[StatusItem, ...] = (
    StatusItem(
        key="DS-01",
        area="Minu asjad · Osakond",
        issue=(
            "Ribade loendurid ja SEIS-silt. Prototüüp kirjutab «8 tegevust» ja "
            "otsustuslugu (konfliktid A5) ütleb, et silt «SEIS» ja sõna "
            "«tegevust» jäävad. 01-EHITUSJUHIS §4 ütleb vastupidist: loendur on "
            "ainult number ja «SEIS» silti ei ole."
        ),
        why="Kaks pakis olevat allikat ütlevad vastupidist.",
        state=DECIDED,
        next_step=(
            "Rakendatud on 01-EHITUSJUHIS §4: paljas arv, ilma «SEIS» sildita. "
            "Prototüübi enda raamid jäid vanaks — kui tellija soovib teisiti, "
            "on muudatus ühes mallis."
        ),
        sources=("01-EHITUSJUHIS §4", "lisad/konfliktid_git_vs_prototuup.md A5"),
    ),
    StatusItem(
        key="DS-02",
        area="Minu asjad",
        issue=(
            "Roheline «✓ Märgi tehtuks» on ridadelt eemaldatud ja klahv X koos "
            "sellega. Marsruut ja teenus complete_work_item jäid alles."
        ),
        why=(
            "Disain keelab ühe klikiga lõpetamise, sest järgmist sammu ei "
            "määrata. Töötavat serveripoolset teenust ei kustutata ilma "
            "eraldi otsuseta."
        ),
        state=DEVIATION,
        next_step=(
            "Otsustada, kas marsruut jääb alles kasutuseta või eemaldatakse "
            "koos teenusega eraldi voorus."
        ),
        sources=("01-EHITUSJUHIS §3.6", "app/matters/urls.py"),
    ),
    StatusItem(
        key="DS-03",
        area="Arvamused",
        issue=(
            "02-EKRAANID §8 ütleb, et vanad /arvamused/ URL-id suunatakse ümber "
            "Teemade arvamuste sektsioonile. Praegune kokku liidetud "
            "arhitektuur (ADR 0047) hoiab need täisväärtuslike sihtkohtadena."
        ),
        why=("Ümbersuunamine kaotaks eraldi filtrid, lehitsemise ja iga olemasoleva järjehoidja."),
        state=OPEN,
        next_step=(
            "Praegune käitumine on säilitatud. Ümbersuunamine tehakse ainult "
            "eraldi kinnitatud otsuse alusel."
        ),
        sources=("02-EKRAANID §8", "docs/adr/0047-arvamused-as-a-section-of-teemad.md"),
    ),
    StatusItem(
        key="DS-04",
        area="Osakond",
        issue=(
            "02-EKRAANID loetleb juhi otsuste all kolm plokki: Vajab sekkumist, "
            "Koormus, Aruandlus. Prototüübis oli kaks ja «Koormus» oli Ülevaate "
            "rail-plokk."
        ),
        why="Kolmandat plokki ei ole kuskil kujundatud ega andmetega kaetud.",
        state=DECIDED,
        next_step=(
            "Lahendatud lehtede liitmisega (ADR 0049). Koormus-plokki ei ole "
            "kummalgi ulatusel: sama küsimusele vastab Meeskond-tabel, ja "
            "rail-i «Vajab sekkumist» kordas nelja arvu, mis on põhitulbas "
            "ridadena. Faktid-rail on kolm plokki: Valdkonnad, Uued teemad, "
            "Aruandlus."
        ),
        sources=(
            "02-EKRAANID §B",
            "docs/adr/0049-one-department-page.md",
            "templates/matters/partials/department_rail.html",
        ),
    ),
    StatusItem(
        key="DS-05",
        area="Osakond",
        issue=(
            "02-EKRAANID ütleb, et «Eesolev» on kolm astet (Sel nädalal, "
            "Järgmine kuu, Hiljem). Vanem prototüüp joonistas neli rühma."
        ),
        why="Rühmitust ei muudeta kirjeldava lause pärast.",
        state=DECIDED,
        next_step=(
            "Kinnitatud kujundus (raam C) ütleb viis akent: Täna, homme, "
            "Järgmine nädal, Ülejäänud kuu, Kaugemal. Need on ehitatud ja nad "
            "jagavad tulevased päris tähtajad täpselt ära — ükski kuupäev ei "
            "ole kahes aknas ega puudu (ADR 0049 §5)."
        ),
        sources=(
            "02-EKRAANID §B",
            "docs/adr/0049-one-department-page.md",
            "docs/adr/0046-two-deadline-groups-a-week-and-the-rest-of-the-month.md",
        ),
    ),
    StatusItem(
        key="DS-06",
        area="Jälgimine",
        issue=(
            "Prototüübi «+ Lisa oluline tähtaeg» nupp osakonna vaates ei tea, "
            "millisele teemale kirje läheb."
        ),
        why=(
            "Kõik jälgimise kirjed kuuluvad ühele teemale ja kirjutamine käib "
            "teema lehelt. Nupp, mis peaks teema ise välja mõtlema, oleks "
            "leiutatud funktsionaalsus."
        ),
        state=DEVIATION,
        next_step=(
            "Nuppu ei lisatud. Kui globaalne lisamine on soovitud, vajab see "
            "teema valikut ja eraldi disaini."
        ),
        sources=("prototüüp raam 19", "app/intelligence/urls.py"),
    ),
    StatusItem(
        key="DS-07",
        area="Statistika",
        issue=(
            "Uus sakiriba nimetab viit sakki (Ülevaade, Teemad, Tegevus, "
            "Ajalugu, Definitsioonid). Rakenduses on lisaks Andmekvaliteet ja "
            "kaks läbipääsu-nimekirja, ning alamsakid on 01-EHITUSJUHIS §9 "
            "järgi teadlikult disainimata."
        ),
        why=("Sakiriba, mis jätaks Andmekvaliteedi välja, peidaks töötava lehe ära ilma otsuseta."),
        state=OPEN,
        next_step=(
            "Ülevaade on ümber ehitatud, alamsakid on puutumata ja "
            "Andmekvaliteet on sakiribal alles. Alamsakkide disain on "
            "tellija poolel."
        ),
        sources=("02-EKRAANID §E", "01-EHITUSJUHIS §9.4", "app/reporting/urls.py"),
    ),
    StatusItem(
        key="DS-08",
        area="Inimese töölaud",
        issue=(
            "Kolleegi ‹ › järjekord: tiimitabeli järjekord või tähestik? "
            "01-EHITUSJUHIS §9.1 jätab lahtiseks."
        ),
        why="Kaks vastust annavad kaks eri navigeerimiskogemust.",
        state=OPEN,
        next_step=(
            "Kasutusel on tiimitabeli järjekord, nagu prototüübis. Vahetus on ühe selektori võrra."
        ),
        sources=("01-EHITUSJUHIS §9.1",),
    ),
    StatusItem(
        key="DS-09",
        area="Saabunud",
        issue=(
            "Vormil on väli «Märkmed vastutajale», millele mudelis vastet ei "
            "ole. 01-EHITUSJUHIS §9.5 küsib lisaks, kas see peaks olema "
            "nähtav kõigile teema nägijatele."
        ),
        why=("Uue veeru lisamine enne nähtavuse otsust lukustaks vastuse vale koha peal."),
        state=DEVIATION,
        next_step=(
            "Märge salvestatakse teema esimese ajajoone sissekandena — "
            "nähtav kõigile, kes teemat näevad, nagu §9.5 praegust käitumist "
            "kirjeldab. Kui vastus on «ainult vastutajale», on see eraldi "
            "mudelimuudatus."
        ),
        sources=("02-EKRAANID §F", "01-EHITUSJUHIS §9.5"),
    ),
    StatusItem(
        key="DS-10",
        area="Dokument",
        issue=(
            "«Tuletatud eelvaade» plokk on disainist välja jäetud. See oli "
            "ainus koht, kus eraldatud tekst kasutajale näha oli."
        ),
        why="Eraldamise torustik, marsruudid ja andmed on puutumata.",
        state=DEVIATION,
        next_step=(
            "Plokk on eemaldatud ainult sellelt lehelt. Kui eraldatud teksti "
            "on vaja lugeda, tuleb sellele anda oma koht."
        ),
        sources=("02-EKRAANID §G", "templates/documents/document_detail.html"),
    ),
    StatusItem(
        key="DS-11",
        area="Minu asjad · Aktiivsed teemad",
        issue=(
            "Prototüübi chip kannab nime «Vajab tähelepanu»; "
            "01-EHITUSJUHIS §4 sõnavara ütleb «Vajab sekkumist»."
        ),
        why="Sama vastuolu kui DS-01, teises komponendis.",
        state=DECIDED,
        next_step="Kasutusel on §4 sõnavara.",
        sources=("01-EHITUSJUHIS §4", "prototüüp raam 01"),
    ),
    StatusItem(
        key="DS-12",
        area="Statistika · Inimesed",
        issue=(
            "Prototüübi rail-plokk «Inimesed» viib iga rea inimese töölauale. "
            "Inimese töölaud on lubatud ainult iseendale või osakonnajuhile."
        ),
        why=(
            "Link, mis enamikule lugejatest annab 404, on leht, mis ütleb "
            "kasutajale «proovi seda» ja teab, et see ei õnnestu."
        ),
        state=DECIDED,
        next_step=(
            "Plokk kuvab lingid ainult siis, kui lugeja neid ka avada saab; "
            "muidu on read ilma linkideta. Arvud on samad."
        ),
        sources=("02-EKRAANID §E", "03-BACKEND §5"),
    ),
    StatusItem(
        key="DS-13",
        area="Teemad · Arvamused",
        issue=(
            "Prototüübi arvamuste plokis on «Täpsem otsing» avaja, mille sisu "
            "ei ole kujundatud — tühi <details>."
        ),
        why=(
            "Arvamuste täpsema otsingu välju ei ole kuskil defineeritud; nende "
            "väljamõtlemine oleks uus otsinguliides."
        ),
        state=DEVIATION,
        next_step=(
            "Avajat ei lisatud. Arvamuste plokil on oma otsinguväli ja täielik "
            "töölaud «Vaata kõiki arvamusi» taga. Kui täpsem otsing on soovitud, "
            "tuleb öelda, milliste väljade järgi."
        ),
        sources=("02-EKRAANID §C", "prototüüp raam 05"),
    ),
    StatusItem(
        key="DS-14",
        area="Jälgimine · mallid",
        issue=(
            "02-EKRAANID nimetab mallideks `tracking/deadlines.html`, "
            "`tracking/entries_into_force.html` ja `tracking/wins.html`. "
            "Need lehed elavad rakenduses `app.intelligence` ja nende mallid "
            "on `templates/intelligence/`."
        ),
        why=(
            "Mallikataloog, mis ei vasta ühelegi rakendusele, on koht, kus "
            "järgmine lugeja neid otsima ei hakka."
        ),
        state=DEVIATION,
        next_step=(
            "Marsruudid on disaini omad (/jalgimine/…), mallide asukoht on "
            "rakenduse oma. Ümbernimetamine on odav, kui seda soovitakse."
        ),
        sources=("02-EKRAANID §D", "app/intelligence/"),
    ),
    StatusItem(
        key="DS-15",
        area="Saabunud · Lisa materjal",
        issue=(
            "Disain jätab vormilt välja hetkeseisu, menetlusliigi ja kogu "
            "ülejäänud osa («Ülejäänud andmed saab lisada teema lehel»). "
            "Nähtavus jäeti alles."
        ),
        why=(
            "Piiratud kirja registreerimine tavalisena ja parandamine hiljem "
            "tähendab, et vahepeal oli see kogu osakonnale nähtav. Ükski selle "
            "vooru muudatus ei laienda ligipääsu."
        ),
        state=DEVIATION,
        next_step=(
            "Kui nähtavus peab ka siit kaduma, on vaja otsust, mis juhtub vahepealse ajaga."
        ),
        sources=("02-EKRAANID §F", "AGENTS.md — Data and security"),
    ),
    StatusItem(
        key="DS-16",
        area="Jälgimine",
        issue=(
            "Prototüübi SEIS-ribadel on figuurid, millele see rakendus ei oska "
            "nimekirja anda: «vastutajata» tähtajad ja «sel kuul» töövõidud."
        ),
        why=(
            "01-EHITUSJUHIS §3.3: kui arvu taga ei ole avatavat päringut, siis "
            "arvu ei kuvata. Uue filtri leiutamine ainult ühe numbri jaoks oleks "
            "uus otsingupind."
        ),
        state=DEVIATION,
        next_step=(
            "Ribad kuvavad neid figuure, millel on oma nimekiri. Kui puuduvad "
            "arvud on vajalikud, tuleb otsustada, millise filtri nad avavad."
        ),
        sources=("01-EHITUSJUHIS §3.3", "prototüüp raamid 19–21"),
    ),
    StatusItem(
        key="DS-17",
        area="Jälgimine · Jõustuvad aktid",
        issue=(
            "Prototüübi tabeli teine veerg kannab päist «Tähtaeg», kuigi selles "
            "on jõustuva akti kirjeldus, mitte tähtaeg."
        ),
        why="Tõenäoliselt kopeerimisviga tähtaegade tabelist.",
        state=OPEN,
        next_step=(
            "Kasutusel on prototüübi sõnastus, sest §4 keelab oma sõnade "
            "leiutamise. Vajab ühte sõna tellijalt."
        ),
        sources=("02-EKRAANID §D", "prototüüp raam 20"),
    ),
    StatusItem(
        key="DS-18",
        area="Jälgimine",
        issue=(
            "Disain joonistab lehed vaikeseisus ega näita olemasolevaid "
            "filtreid (ajaline suund, allikas, aasta)."
        ),
        why=(
            "Need on ainus tee möödunud ja aastapõhiste kirjeteni. Ainsa tee "
            "eemaldamine populatsioonini ei ole visuaalne muudatus."
        ),
        state=DEVIATION,
        next_step=(
            "Filtrid on säilitatud sakiriba all. Sektsioonid asendavad ajalise suuna vaikevaates."
        ),
        sources=("02-EKRAANID §D", "app/intelligence/selectors.py"),
    ),
    StatusItem(
        key="DS-19",
        area="Statistika · Ülevaade",
        issue=(
            "Disaini kuutabelil on kolm veergu (uued teemad · arvamusi välja · "
            "lõpetatud) ja SEIS-ribal viies figuur «lõpetatud teemat». "
            "Kataloogis on kuupõhine definitsioon ainult uutel teemadel, ja "
            "lõpetamise kuupäeva järgi register ei filtreeri."
        ),
        why=("Uue mõõdiku lisamine kataloogi on definitsiooniotsus, mitte malli küsimus."),
        state=OPEN,
        next_step=(
            "Tabel kuvab selle veeru, millel on definitsioon; riba nelja "
            "figuuri, millel on nimekiri. Puuduvad vajavad kas uut mõõdikut "
            "või uut registrifiltrit."
        ),
        sources=("02-EKRAANID §E", "app/reporting/metric_catalogue.py"),
    ),
    StatusItem(
        key="DS-21",
        area="Statistika · Ülevaade",
        issue=(
            "Sektsioon «Muutus eelmise aastaga» kadus koos graafikutega. Kaks "
            "mõõdikut — uute teemade ja arhiivi arvamuste aastane muutus — on "
            "kataloogis alles, aga neid ei kuvata enam kuskil."
        ),
        why=(
            "Disainitud ülevaade on riba, kolm tabelit ja loendurite tulp. "
            "Võrdluskaarti seal ei ole ja alamsakke ei tohi ise ümber "
            "kujundada."
        ),
        state=OPEN,
        next_step=(
            "Definitsioonid, nende versioonid ja reeglid (mõlemad pooled sama "
            "kuupäevaga lõigatud; null-eelmine periood ei anna protsenti) on "
            "puutumata ja testitud. Kui võrdlust on vaja näha, tuleb otsustada, "
            "millisel sakil."
        ),
        sources=("02-EKRAANID §E", "tests/test_reporting_archive_trends.py"),
    ),
    StatusItem(
        key="DS-20",
        area="Teema · Koja seisukoht",
        issue=("«Põhjendus» plokki enam ei kuvata ei rail-kaardil ega seisukoha kaardil."),
        why=(
            "Väli on alles, seda muudetakse ja salvestatakse sama vormiga. "
            "Kadus ainult selle kuvamine."
        ),
        state=DEVIATION,
        next_step=(
            "Kui põhjendust on vaja lugeda ilma vormi avamata, tuleb sellele anda oma koht."
        ),
        sources=("02-EKRAANID §C", "templates/matters/matter_position.html"),
    ),
    StatusItem(
        key="DS-22",
        area="Otsing · visuaaltestid",
        issue=(
            "Otsingutulemuste leht kuvab kirjeid, mille tekstis on iga käivituse "
            "kohta uus UUID. Selle tõttu erineb `otsing` võrdluspilt kahe sama "
            "koodi peal tehtud CI-käivituse vahel 0,04% võrra."
        ),
        why=(
            "See on vanem kui v2 disain ja jääb praegu alla 0,2% lävendi, nii et "
            "test on roheline. Kaks eraldi asja lahenevad koos: kas fikstuur "
            "annab püsivad tunnused või ei kuva leht neid üldse — kumbki ei ole "
            "selle vooru töö."
        ),
        state=OPEN,
        next_step=(
            "Enne järgmist otsingulehe muudatust: otsustada, kas identifikaator "
            "on lugejale vajalik. Kui ei ole, kaob triiv koos sellega; kui on, "
            "peab seeme need fikseerima."
        ),
        sources=("e2e/test_ui_regression.py", "e2e/baselines/otsing.png"),
    ),
    StatusItem(
        key="DS-23",
        area="Tühjad seisud · neli lauset",
        issue=(
            "Neli tühja seisu on sõnastatud siin, mitte disainis: «Muudatusi ei "
            "ole.» (Minu asjad, Viimati muudetud), «Selles vaates ei ole ühtegi "
            "teemat.» (Minu asjad, Aktiivsed teemad chipi all), «Valitud vaates "
            "ei ole midagi loendada.» (Statistika segmenditabel) ja «Ühelgi "
            "inimesel ei ole avatud teemasid.» (Statistika ülevaate rail-plokk "
            "«Inimesed»)."
        ),
        why=(
            "Prototüübi #tuhjad kaader ütleb «viis kohta, kus andmeid ei ole» ja "
            "annab viis lauset. Need neli kohta ei ole nende hulgas, aga plokid "
            "ise on disainis olemas ja tühjana peavad nad midagi ütlema. "
            "Sõnastus on neutraalne ja faktiline, mitte selgitav — aga see on "
            "siiski meie sõnastus, mitte tellija oma."
        ),
        state=DEVIATION,
        next_step=(
            "Kinnitada või asendada neli lauset. Ainsad kohad, kus neid muuta "
            "tuleb, on mallid, kus nad praegu seisavad; loogikat see ei puuduta."
        ),
        sources=(
            "prototüüp #tuhjad",
            "templates/matters/my_work.html",
            "templates/reporting/_segment_table.html",
            "templates/reporting/overview.html",
        ),
    ),
    StatusItem(
        key="DS-24",
        area="Osakond · Seis",
        issue=(
            "Seis-riba kuues figuur «N arvamust välja · 7 p» loendab seitsme "
            "päeva akent, aga Arvamuste tööruum oskab filtreerida ainult aasta "
            "ja kuu järgi. Link avab seega laiema nimekirja kui arv, mille "
            "kõrval ta seisab."
        ),
        why=(
            "Iga arv on lubadus, et tema taga on täpselt see nimekiri, mida ta "
            "loendas. Siin ei ole — ja kolm lahendust on kõik otsused, mitte "
            "malli küsimused: kas Arvamuste tööruum saab kuupäevavahemiku "
            "filtri, või muutub akna pikkus millekski, mida praegused filtrid "
            "väljendavad, või jääb figuur lingita arvuks (nagu Meeskonna kolm "
            "ajaloolist veergu)."
        ),
        state=DECIDED,
        next_step=(
            "Figuur on nüüd lingita arv — sama kohtlemine, mille saavad "
            "Meeskonna kolm ajaloolist veergu: aus arv on parem kui link "
            "teistsugusele nimekirjale. Uut filtrit ega uut päringusemantikat "
            "ei ole lisatud, sõnastus ja arv on kujunduse omad. Kui link on "
            "siiski soovitud, on see otsus anda Arvamuste tööruumile "
            "kuupäevavahemiku filter."
        ),
        sources=(
            "01-EHITUSPROMPT §3.2",
            "app/matters/department_dashboard.py, `seis_figures`",
            "app/submissions/workspace.py, `SentFilters`",
        ),
    ),
    StatusItem(
        key="DS-25",
        area="Osakond · Tehtud",
        issue=(
            "Ülevaate «Viimased muudatused» voog ei ole enam ühelgi lehel. "
            "Kinnitatud kujundus (raam C) ütleb, et perioodi küsimusele vastab "
            "Tehtud, ja et eraldi voogu ei ole; Tehtud loeb kanoonilistest "
            "kirjetest (arvamus, suletud teema, töövõit, sissekanne), voog aga "
            "`ChangeEvent`-idest."
        ),
        why=(
            "Voog kandis paar asja, mida Tehtud ei kanna — hetkeseisu muutus, "
            "vastutaja määramine, kaasamise lisamine. Uut sektsiooni nende "
            "jaoks ei tehtud (see oleks viies sisusüsteem ühel lehel) ega "
            "kuuendat liigifiltrit (`DigestRow.kind` loeb kirjeid, mitte "
            "sündmusi). Lugemismudel `activity_feed` on alles ja testitud."
        ),
        state=OPEN,
        next_step=(
            "Otsustada, kas need sündmused on Tehtud-plokis vajalikud. Kui jah, "
            "on see uus liik, mis loeb `ChangeEvent`-e — see on definitsiooni-, "
            "mitte malliotsus. Kui ei, võib `activity_feed` koos oma testidega "
            "eraldi voorus ära kaduda."
        ),
        sources=(
            "01-EHITUSPROMPT §3.3",
            "docs/adr/0049-one-department-page.md",
            "app/matters/overview.py, `activity_feed`",
        ),
    ),
    StatusItem(
        key="DS-26",
        area="Osakond · Eesolev",
        issue=(
            "Tähtaegade kolme akna lugemismudel (`deadline_windows`, "
            "`deadline_groups`, `DeadlineGroup`, `real_deadlines`) ei ole enam "
            "ühelgi lehel — /osakond/ joonistab viie akna paneeli "
            "(`department_dashboard.upcoming_groups`, ADR 0049 §5). Mudel ja "
            "selle kakskümmend testi on alles."
        ),
        why=(
            "Eemaldamine tähendaks otsust, et `tests/test_deadline_grouping.py` "
            "on elava paneeli enda testidega täielikult kaetud. Kattuvus on "
            "suur, aga see ei ole tõestatult täielik — ja suure eemalduse lõpus "
            "tehtud kattuvusotsus on just see, mis vaikselt ühe invariandi "
            "kaotab. Sama põhjus, mis hoiab alles `activity_feed`-i (DS-25)."
        ),
        state=DECIDED,
        next_step=(
            "Tehtud. Ükski marsruuditud leht ei lugenud mudelit: "
            "`deadline_windows`, `deadline_groups` ja `DeadlineGroup` olid "
            "ainult testide kutsuda. `real_deadlines` ei olnud osa surnud "
            "mudelist — definitsioon elab `work_items`-is ja elav paneel loeb "
            "seda, nii et kadus ainult `overview`-i taasekspordi nimi. "
            "Invariandid võrreldi ükshaaval ja kolm üldist tõsteti viie akna "
            "mudeli peale: akende järjestikkusus üle aasta päevade ja "
            "*Ülejäänud kuu* lõpp päris kuu lõpus (viimast ei katnud ükski "
            "elav test), ning akna ridade järjekord ja selle püsivus kahe "
            "lugemise vahel. Ülejäänu oli kolme akna oma geomeetria — "
            "kalendrinädala aken, tühi keskmine aken, üherealine *Kaugemal* — "
            "mis on elavas mudelis teadlikult teisiti. Mudel koos oma "
            "testidega on eemaldatud."
        ),
        sources=(
            "docs/adr/0049-one-department-page.md",
            "app/matters/department_dashboard.py, `upcoming_groups`",
            "tests/test_deadline_grouping.py",
        ),
    ),
)
