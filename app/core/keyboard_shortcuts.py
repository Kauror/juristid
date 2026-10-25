"""The keyboard shortcuts Juristid really has, as `/kiirklahvid/` lists them.

One page that says what the keys do, instead of hint strips scattered over the
screens people use every day (the owner's brief, 2026-10-25). The screens keep
the shortcuts and lose the decoration; this is where somebody looks them up.

**Every row names the code that answers it.** `evidence` is a literal piece of
the script that implements the key (compared with runs of whitespace folded to
one space, so a re-indent is not a failure), and `tests/test_keyboard_shortcuts.py`
fails when it is no longer there. A reference page that goes on promising a
key after its handler was deleted is worse than no page, and it is exactly the
kind of drift nobody notices by reading the page.

Only keys Juristid itself handles are listed. The browser's own keys — Tab,
Enter on a link, Space on a button — work everywhere and are nobody's news;
listing them would bury the five that are. Every key here also has a click
equivalent on the screen (AGENTS.md), and each row says what that is where it
is not obvious.

`mod` in a chord is the platform's modifier: `Ctrl` on Windows and Linux, `⌘`
on macOS, because every handler here reads `ctrlKey || metaKey`.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The placeholder for the platform modifier inside a chord.
MOD = "mod"


@dataclass(frozen=True)
class Shortcut:
    #: Alternatives, each a chord of key labels; ``MOD`` is Ctrl / ⌘.
    keys: tuple[tuple[str, ...], ...]
    action: str
    #: Where it does not apply, and the click equivalent where it is not obvious.
    note: str
    #: ``(file under static/js/, literal text in it)`` — the code that answers.
    evidence: tuple[tuple[str, str], ...]

    @property
    def uses_modifier(self) -> bool:
        return any(MOD in chord for chord in self.keys)

    def chords_for(self, modifier: str) -> list[list[str]]:
        return [[modifier if key == MOD else key for key in chord] for chord in self.keys]

    @property
    def windows(self) -> list[list[str]]:
        return self.chords_for("Ctrl")

    @property
    def mac(self) -> list[list[str]]:
        return self.chords_for("⌘")


@dataclass(frozen=True)
class ShortcutGroup:
    slug: str
    title: str
    lead: str
    shortcuts: tuple[Shortcut, ...]


GROUPS: tuple[ShortcutGroup, ...] = (
    ShortcutGroup(
        slug="koikjal",
        title="Kõikjal",
        lead="Töötavad igal Juristidi lehel.",
        shortcuts=(
            Shortcut(
                keys=((MOD, "K"),),
                action="Viib kursori päise otsinguväljale ja valib seal oleva teksti.",
                note=(
                    "Töötab ka siis, kui kirjutad parasjagu mõnda välja. "
                    "Sama teeb klõps otsinguväljal."
                ),
                evidence=(
                    (
                        "app.js",
                        '(event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k"',
                    ),
                    ("app.js", 'document.getElementById("global-search")'),
                ),
            ),
            Shortcut(
                keys=(("Esc",),),
                action=(
                    "Sulgeb avatud menüü: päise «Veel», teema «⋯ Rohkem tegevusi», "
                    "«Täpsem otsing» ja tabelite valikumenüüd. Kursor läheb tagasi "
                    "nupule, millega menüü avasid."
                ),
                note="Sama teeb klõps menüüst väljas.",
                evidence=(
                    (
                        "app.js",
                        'var ESCAPABLE = "details.topnav__more, details.headmenu, '
                        'details.inlineedit, details.filterpanel";',
                    ),
                    ("ux.js", 'event.target.closest("details[data-uxpopover][open]")'),
                ),
            ),
        ),
    ),
    ShortcutGroup(
        slug="otsing",
        title="Otsingu soovitused",
        lead="Päise otsinguväljas, kui kirjutamise ajal avanevad soovitused.",
        shortcuts=(
            Shortcut(
                keys=(("↓",), ("↑",)),
                action="Liigub soovituste loendis järgmisele või eelmisele reale.",
                note="Ainult siis, kui soovitused on avatud.",
                evidence=(("app.js", "if (panel.hidden || !options.length) {"),),
            ),
            Shortcut(
                keys=(("Enter",),),
                action="Avab valitud soovituse.",
                note="Kui ükski rida pole valitud, teeb Enter tavalise otsingu.",
                evidence=(("app.js", "window.location.assign(target);"),),
            ),
            Shortcut(
                keys=(("Esc",),),
                action="Sulgeb soovitused; kirjutatud tekst jääb väljale alles.",
                note="",
                evidence=(("app.js", 'if (event.key === "Escape") { if (!panel.hidden) {'),),
            ),
        ),
    ),
    ShortcutGroup(
        slug="teema",
        title="Teema leht ja kohapeal avanevad vormid",
        lead=(
            "Teema lehel avanevad muutmisvormid otse väärtuse juures — kokkuvõte, "
            "päise väljad, «Praegune tegevus», «Lisa teemale» vormid."
        ),
        shortcuts=(
            Shortcut(
                keys=(("L",),),
                action=(
                    "Avab tegevuse kirja panemise vormi: kui teemal on käimasolev "
                    "tegevus, siis «✓ Tehtud» välja «Mida tegid?», muidu "
                    "«+ Lisa tegevus»."
                ),
                note=(
                    "Ainult teema lehel ja ainult siis, kui kursor ei ole tekstiväljas "
                    "ega avatud vormis. Ei tee midagi, kui sul pole teemale "
                    "kirjutamise õigust. Sama teevad nupud «✓ Tehtud» ja «+ Lisa tegevus»."
                ),
                evidence=(
                    ("ux.js", 'if (event.key !== "l" && event.key !== "L") {'),
                    ("ux.js", 'document.getElementById("lisa-jargmine")'),
                ),
            ),
            Shortcut(
                keys=((MOD, "Enter"),),
                action="Salvestab avatud vormi.",
                note=(
                    "Kursor peab olema vormi sees. Töötab ka teemade loendi "
                    "«Täpsem otsing» paneelis. Sama teeb vormi nupp «Salvesta»."
                ),
                evidence=(
                    ("app.js", 'event.target.closest("details form, form[data-addform]")'),
                    ("app.js", 'event.target.closest("form[data-composer]")'),
                    ("app.js", '(event.ctrlKey || event.metaKey) && event.key === "Enter"'),
                ),
            ),
            Shortcut(
                keys=(("Esc",),),
                action=(
                    "Katkestab avatud muutmisvormi ja sulgeb selle; salvestamata "
                    "muudatused jäävad salvestamata. «Praegune tegevus» vormis peidab "
                    "Esc avatud lisaväljad."
                ),
                note=(
                    "Kui vormis on avatud kalender või soovituste loend, sulgeb esimene "
                    "Esc ainult selle ja teine Esc vormi. "
                    "Sama teeb vormi nupp «Loobu» või «Tühista»."
                ),
                evidence=(
                    ("app.js", 'if (event.key !== "Escape" || event.defaultPrevented) {'),
                    ("app.js", 'composer.querySelectorAll("[data-reveals]")'),
                ),
            ),
        ),
    ),
    ShortcutGroup(
        slug="minu-asjad",
        title="Minu asjad",
        lead="Tööde loendis.",
        shortcuts=(
            Shortcut(
                keys=(("J",),),
                action="Valib järgmise töö. Esimene vajutus valib loendi esimese.",
                note="Ei tööta, kui kursor on tekstiväljas.",
                evidence=(("ux.js", 'if (key !== "j" && key !== "k" && event.key !== "Enter") {'),),
            ),
            Shortcut(
                keys=(("K",),),
                action="Valib eelmise töö. Esimene vajutus valib loendi viimase.",
                note="Ei tööta, kui kursor on tekstiväljas.",
                evidence=(("ux.js", '(key === "j" ? 1 : -1)'),),
            ),
            Shortcut(
                keys=(("Enter",),),
                action="Avab valitud töö teema.",
                note="Sama teeb klõps töö pealkirjal.",
                evidence=(("ux.js", "open.click();"),),
            ),
        ),
    ),
    ShortcutGroup(
        slug="kalender",
        title="Kuupäevavalija",
        lead="Kalendris, mis avaneb kuupäevavälja kõrval olevast nupust «Ava kalender».",
        shortcuts=(
            Shortcut(
                keys=(("←",), ("→",)),
                action="Eelmine või järgmine päev.",
                note="",
                evidence=(("app.js", 'case "ArrowLeft":'),),
            ),
            Shortcut(
                keys=(("↑",), ("↓",)),
                action="Sama nädalapäev eelmisel või järgmisel nädalal.",
                note="",
                evidence=(("app.js", "to = addDays(on, -7);"),),
            ),
            Shortcut(
                keys=(("Home",), ("End",)),
                action="Nädala esmaspäev või pühapäev.",
                note="",
                evidence=(("app.js", "to = addDays(on, -mondayIndex(on));"),),
            ),
            Shortcut(
                keys=(("Page Up",), ("Page Down",)),
                action="Eelmine või järgmine kuu.",
                note="Koos klahviga Shift liigub aasta kaupa.",
                evidence=(("app.js", "to = addMonths(on, event.shiftKey ? -12 : -1);"),),
            ),
            Shortcut(
                keys=(("Esc",),),
                action="Sulgeb kalendri ja viib kursori tagasi kuupäevaväljale.",
                note="",
                evidence=(("app.js", 'if (event.key === "Escape" && !panel.hidden) {'),),
            ),
        ),
    ),
)
