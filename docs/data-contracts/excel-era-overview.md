# Ajastulepingute ülevaade

**Genereeritud fail.** Ära muuda seda käsitsi — allikaks on samas kaustas
olevad `excel-era-<aasta>.toml` failid ja selle kirjutab uuesti
`python manage.py check_era_contracts`.

Lepingu skeemi versioon: `1.0`.

## Ajastud

| Aasta | Ajastu | Pealkirjarida | Veerge | Vastaspool |
| --- | --- | ---: | ---: | --- |
| 2011 | 2011-2017 | 1 | 8 | KELLELT (saatja) |
| 2012 | 2011-2017 | 1 | 8 | KELLELT (saatja) |
| 2013 | 2011-2017 | 1 | 8 | KELLELT (saatja) |
| 2014 | 2011-2017 | 1 | 8 | KELLELT (saatja) |
| 2015 | 2011-2017 | 1 | 8 | KELLELT (saatja) |
| 2016 | 2011-2017 | 1 | 8 | KELLELT (saatja) |
| 2017 | 2011-2017 | 1 | 8 | KELLELT (saatja) |
| 2018 | 2018-2019 | 1 | 10 | KELLELT (saatja) |
| 2019 | 2018-2019 | 1 | 10 | KELLELT (saatja) |
| 2020 | 2020-2022 | 1 | 10 | KELLELE (adressaat) |
| 2021 | 2020-2022 | 2 | 10 | KELLELE (adressaat) |
| 2022 | 2020-2022 | 1 | 11 | KELLELE (adressaat) |
| 2023 | 2023-2024 | 1 | 11 | KELLELE (adressaat) |
| 2024 | 2023-2024 | 1 | 11 | KELLELE (adressaat) |
| 2025 | 2025 | 1 | 12 | KELLELE (adressaat) |
| 2026 | 2026 | 1 | 12 | KELLELE (adressaat) |

## 2011

Registri põhikuju. Kaheksa veergu. Vastaspool on KELLELT ehk saatja. Liikmete tagasiside veerge ei ole, Hetkeseisu ei ole, Järgmiseks ei ole. Nende puudumine on ajastu omadus, mitte puuduv andmestik: nendel aastatel ei olnud registris etapi ega järgmise tegevuse mõistet, seega imporditud rida ei saa neid välju kanda ja neid ei tuletata.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELT` | `source_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |

## 2012

Registri põhikuju. Kaheksa veergu. Vastaspool on KELLELT ehk saatja. Liikmete tagasiside veerge ei ole, Hetkeseisu ei ole, Järgmiseks ei ole. Nende puudumine on ajastu omadus, mitte puuduv andmestik: nendel aastatel ei olnud registris etapi ega järgmise tegevuse mõistet, seega imporditud rida ei saa neid välju kanda ja neid ei tuletata.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELT` | `source_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |

## 2013

Registri põhikuju. Kaheksa veergu. Vastaspool on KELLELT ehk saatja. Liikmete tagasiside veerge ei ole, Hetkeseisu ei ole, Järgmiseks ei ole. Nende puudumine on ajastu omadus, mitte puuduv andmestik: nendel aastatel ei olnud registris etapi ega järgmise tegevuse mõistet, seega imporditud rida ei saa neid välju kanda ja neid ei tuletata.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELT` | `source_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |

## 2014

Registri põhikuju. Kaheksa veergu. Vastaspool on KELLELT ehk saatja. Liikmete tagasiside veerge ei ole, Hetkeseisu ei ole, Järgmiseks ei ole. Nende puudumine on ajastu omadus, mitte puuduv andmestik: nendel aastatel ei olnud registris etapi ega järgmise tegevuse mõistet, seega imporditud rida ei saa neid välju kanda ja neid ei tuletata.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELT` | `source_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |

## 2015

Registri põhikuju. Kaheksa veergu. Vastaspool on KELLELT ehk saatja. Liikmete tagasiside veerge ei ole, Hetkeseisu ei ole, Järgmiseks ei ole. Nende puudumine on ajastu omadus, mitte puuduv andmestik: nendel aastatel ei olnud registris etapi ega järgmise tegevuse mõistet, seega imporditud rida ei saa neid välju kanda ja neid ei tuletata.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELT` | `source_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |

## 2016

Registri põhikuju. Kaheksa veergu. Vastaspool on KELLELT ehk saatja. Liikmete tagasiside veerge ei ole, Hetkeseisu ei ole, Järgmiseks ei ole. Nende puudumine on ajastu omadus, mitte puuduv andmestik: nendel aastatel ei olnud registris etapi ega järgmise tegevuse mõistet, seega imporditud rida ei saa neid välju kanda ja neid ei tuletata.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELT` | `source_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |

## 2017

Registri põhikuju. Kaheksa veergu. Vastaspool on KELLELT ehk saatja. Liikmete tagasiside veerge ei ole, Hetkeseisu ei ole, Järgmiseks ei ole. Nende puudumine on ajastu omadus, mitte puuduv andmestik: nendel aastatel ei olnud registris etapi ega järgmise tegevuse mõistet, seega imporditud rida ei saa neid välju kanda ja neid ei tuletata.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELT` | `source_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |

## 2018

Kümme veergu. Vastaspool on endiselt KELLELT ehk saatja. Ilmuvad liikmete tagasiside arvud, mis jäävad tooreks ja edasi lükatuks. Hetkeseisu ega Järgmiseks veergu ei ole.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELT` | `source_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |
| I | `LIIKMETE ARV, KES ANDSID TAGASISIDET` | `member_feedback_responded` | `count` | edasi lükatud | Tühi ei ole null. Mõõdetud null on eraldi fakt ja need kaks ei tohi kokku langeda. |
| J | `LIIKMETE ARV, KELLELT OTSE KÜSISIME TAGASISIDET` | `member_feedback_requested` | `count` | edasi lükatud | Tühi ei ole null. |

## 2019

Kümme veergu. Vastaspool on endiselt KELLELT ehk saatja. Ilmuvad liikmete tagasiside arvud, mis jäävad tooreks ja edasi lükatuks. Hetkeseisu ega Järgmiseks veergu ei ole.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELT` | `source_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |
| I | `LIIKMETE ARV, KES ANDSID TAGASISIDET` | `member_feedback_responded` | `count` | edasi lükatud | Tühi ei ole null. Mõõdetud null on eraldi fakt ja need kaks ei tohi kokku langeda. |
| J | `LIIKMETE ARV, KELLELT OTSE KÜSISIME TAGASISIDET` | `member_feedback_requested` | `count` | edasi lükatud | Tühi ei ole null. |

## 2020

Vastaspoole veerg muutub: KELLELT asemel on KELLELE ja tähendus pöördub saatjast adressaadiks. Neid kahte ei ühendata. Hetkeseisu ega Järgmiseks veergu ei ole veel.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELE` | `addressee_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |
| I | `LIIKMETE ARV, KES ANDSID TAGASISIDET` | `member_feedback_responded` | `count` | edasi lükatud | Tühi ei ole null. Mõõdetud null on eraldi fakt ja need kaks ei tohi kokku langeda. |
| J | `LIIKMETE ARV, KELLELT OTSE KÜSISIME TAGASISIDET` | `member_feedback_requested` | `count` | edasi lükatud | Tühi ei ole null. |

## 2021

Vastaspoole veerg muutub: KELLELT asemel on KELLELE ja tähendus pöördub saatjast adressaadiks. Neid kahte ei ühendata. Hetkeseisu ega Järgmiseks veergu ei ole veel.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELE` | `addressee_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |
| I | `LIIKMETE ARV, KES ANDSID TAGASISIDET` | `member_feedback_responded` | `count` | edasi lükatud | Tühi ei ole null. Mõõdetud null on eraldi fakt ja need kaks ei tohi kokku langeda. |
| J | `LIIKMETE ARV, KELLELT OTSE KÜSISIME TAGASISIDET` | `member_feedback_requested` | `count` | edasi lükatud | Tühi ei ole null. |

## 2022

Vastaspoole veerg muutub: KELLELT asemel on KELLELE ja tähendus pöördub saatjast adressaadiks. Neid kahte ei ühendata. Hetkeseisu ega Järgmiseks veergu ei ole veel.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELE` | `addressee_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |
| I | `LIIKMETE ARV, KES ANDSID TAGASISIDET` | `member_feedback_responded` | `count` | edasi lükatud | Tühi ei ole null. Mõõdetud null on eraldi fakt ja need kaks ei tohi kokku langeda. |
| J | `LIIKMETE ARV, KELLELT OTSE KÜSISIME TAGASISIDET` | `member_feedback_requested` | `count` | edasi lükatud | Tühi ei ole null. |
| K | _(pealkirjata)_ | `unknown` | `raw` | tundmatu | Tühja ja täidetud lahtri vahe on säilitatud, kuid kumbki ei kanna teadaolevat tähendust. |

## 2023

Ilmub HETKESEIS. Kasutus on hõre ja ebaühtlane, 2024 sisaldab vabatekstilisi variante. Ainult täpne ülevaadatud vaste tohib täita Matter.stage või Matter.disposition; tundmatu väärtus jääb tooreks ja saab ülevaatuse märke. Hägusat seisundite normaliseerimist ei tehta.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELE` | `addressee_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |
| I | `LIIKMETE ARV, KES ANDSID TAGASISIDET` | `member_feedback_responded` | `count` | edasi lükatud | Tühi ei ole null. Mõõdetud null on eraldi fakt ja need kaks ei tohi kokku langeda. |
| J | `LIIKMETE ARV, KELLELT OTSE KÜSISIME TAGASISIDET` | `member_feedback_requested` | `count` | edasi lükatud | Tühi ei ole null. |
| K | `HETKESEIS` | `legacy_status` | `status_label` | kanooniline | Tühi tähendab, et seisu ei märgitud. |

## 2024

Ilmub HETKESEIS. Kasutus on hõre ja ebaühtlane, 2024 sisaldab vabatekstilisi variante. Ainult täpne ülevaadatud vaste tohib täita Matter.stage või Matter.disposition; tundmatu väärtus jääb tooreks ja saab ülevaatuse märke. Hägusat seisundite normaliseerimist ei tehta.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELE` | `addressee_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |
| I | `LIIKMETE ARV, KES ANDSID TAGASISIDET` | `member_feedback_responded` | `count` | edasi lükatud | Tühi ei ole null. Mõõdetud null on eraldi fakt ja need kaks ei tohi kokku langeda. |
| J | `LIIKMETE ARV, KELLELT OTSE KÜSISIME TAGASISIDET` | `member_feedback_requested` | `count` | edasi lükatud | Tühi ei ole null. |
| K | `HETKESEIS` | `legacy_status` | `status_label` | kanooniline | Tühi tähendab, et seisu ei märgitud. |

## 2025

Ilmub JÄRGMISEKS, kuid ainult osal ridadest. Tekst säilib sõna-sõnalt ja seda ei teisendata automaatselt NextActioniks. Hetkeseisu vasted on ajastupõhised.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELE` | `addressee_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |
| I | `LIIKMETE ARV, KES ANDSID TAGASISIDET` | `member_feedback_responded` | `count` | tuletatud | Tühi ei ole null. Mõõdetud null on eraldi fakt ja need kaks ei tohi kokku langeda. |
| J | `LIIKMETE ARV, KELLELT OTSE KÜSISIME TAGASISIDET` | `member_feedback_requested` | `count` | tuletatud | Tühi ei ole null. |
| K | `HETKESEIS` | `legacy_status` | `status_label` | kanooniline | Tühi tähendab, et seisu ei märgitud. |
| L | `JÄRGMISEKS` | `next_action_text` | `text` | edasi lükatud | Tühi tähendab, et järgmist sammu ei ole kirja pandud. |

## 2026

Praegune standardiseeritud tööstruktuur. Kõik algne tekst säilib. Igast reast ei saa automaatselt FULL-kirjet: kirje liik kirjeldab töö seisu, mitte kalendriaastat, ja aktiivse hulga kinnitab inimene.

| Veerg | Pealkiri | Kanooniline väli | Parser | Kaal | Tühja tähendus |
| --- | --- | --- | --- | --- | --- |
| A | `NR` | `matter_reference` | `human_reference` | kanooniline | Tühi tähendab, et reale ei ole kunagi viidet antud. Selline rida ei ole teemarida. |
| B | `TEEMA` | `title` | `text` | kanooniline | Tühi pealkiri on andmekvaliteedi märge, mitte tühi teema. Ilma pealkirjata rida ei impordi, vaid läheb ülevaatusse. |
| C | `ÕIGUSAKT` | `legal_instrument` | `raw` | edasi lükatud | Tühi tähendab, et liiki ei märgitud. |
| D | `SISSE` | `received_date` | `date` | kanooniline | Tühi ei ole epohh ega null. Tühi tähendab, et saabumiskuupäeva ei ole kirjas. |
| E | `ARVAMUSE TÄHTAEG` | `response_deadline` | `date` | kanooniline | Tühi tähendab, et tähtaeg ei ole kirjas — mitte et tähtaega ei olnud. |
| F | `VÄLJA` | `opinion_sent_date` | `date` | edasi lükatud | Tühi tähendab, et väljasaatmise kuupäeva ei ole kirjas. |
| G | `KELLELE` | `addressee_organisation` | `text` | valikuline | Tühi tähendab teadmata asutust, mitte asutuse puudumist. |
| H | `VASTUTAJA` | `owner_name` | `text` | valikuline | Tühi tähendab, et vastutajat ei ole kirjas. |
| I | `LIIKMETE ARV, KES ANDSID TAGASISIDET` | `member_feedback_responded` | `count` | tuletatud | Tühi ei ole null. Mõõdetud null on eraldi fakt ja need kaks ei tohi kokku langeda. |
| J | `LIIKMETE ARV, KELLELT OTSE KÜSISIME TAGASISIDET` | `member_feedback_requested` | `count` | tuletatud | Tühi ei ole null. |
| K | `HETKESEIS` | `legacy_status` | `status_label` | kanooniline | Tühi tähendab, et seisu ei märgitud. |
| L | `JÄRGMISEKS` | `next_action_text` | `text` | edasi lükatud | Tühi tähendab, et järgmist sammu ei ole kirja pandud. |
