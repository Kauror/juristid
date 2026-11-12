"""Lovable's vocabulary, as Lovable writes it: a snapshot, no data (audit §D.2).

Copied read-only from the Lovable project «Loo Command Center» at commit
`LOVABLE_COMMIT` (04.11.2026, the head the 11.11 audit read; no edit after it):

* `src/lib/topicForm/config.ts` — `OIGUSAKTI_LIIGID`, the top-level category
  «Muu» that `validateTopicForm` accepts as final, `FORM_HETKESEISUD`, and the
  OneNote link schemes the form accepts (``/^(https?|onenote):$/``);
* `drizzle/migrations/0015_canonical_topic_write_layer.sql` — `save_topic`'s
  commencement kinds `c_liigid` and the two that require a date;
* `src/lib/dashboard/logic.ts:185-231` — how Lovable reads its opinion status
  (`isEiSaatnud` is a substring test; `rawArvamuseStaatus` reads `d.m.yy`,
  `d/m/yyyy`, `d-m-yyyy` and ISO days as «saadetud»), quoted in the audit
  (§A.1 A11) and restated here as value classes.

Then, for each value, **what Juristid has decided to do with it**
(`TREATMENT`): mapped, or intentionally held with the decision that says so,
or still pending an owner decision (the audit's LV-01/LV-02/LV-03). LC-V1
(`test_vocabulary_coverage.py`) proves the table is complete and that the
engine does what each decided row says; a pending row is a strict xfail that
flips when the owner decides and the row is updated.

Nothing here is read from the engine: the engine is what is checked against it.
"""

from __future__ import annotations

from typing import Final

#: The Lovable commit every constant below was read at.
LOVABLE_COMMIT: Final = "fe9cc1d8"

# ---------------------------------------------------------------------------
# config.ts
# ---------------------------------------------------------------------------

#: `OIGUSAKTI_LIIGID`: the eight final legal-act classes.
OIGUSAKTI_LIIGID: Final = (
    "VTK",
    "seadus",
    "määrus",
    "muu siseriiklik",
    "ELi konsultatsioon",
    "ELi direktiiv",
    "ELi määrus",
    "muu ELi dokument",
)
#: The top-level category «Muu» is itself final (`!alam && v.oigusakt !== "Muu"`).
OIGUSAKT_MUU: Final = "Muu"
#: Every `oigusakt` the form can save (blank is «not stated»).
OIGUSAKTID: Final = (*OIGUSAKTI_LIIGID, OIGUSAKT_MUU)

#: `FORM_HETKESEISUD`: the eleven stages the form offers.
FORM_HETKESEISUD: Final = (
    "idee",
    "kooskõlastusringil",
    "valitsuses",
    "Riigikogus",
    "jõustumise ootel",
    "jõustunud",
    "Eesti seisukoht koostamisel",
    "ELi menetluses",
    "ELi õiguse ülevõtmise ootel",
    "rohkem ei tegele",
    "muu",
)
#: `teemaOlekFor`: the two stages at which Lovable calls a topic «Lõpetatud».
LOVABLE_FINISHED_STAGES: Final = ("jõustunud", "rohkem ei tegele")

#: The URL schemes `validateTopicForm` accepts for `onenote`.
ONENOTE_LINK_SCHEMES: Final = ("http", "https", "onenote")

# ---------------------------------------------------------------------------
# 0015_canonical_topic_write_layer.sql
# ---------------------------------------------------------------------------

#: `c_liigid`: the commencement kinds `save_topic` accepts.
C_LIIGID: Final = (
    "Kindla kuupäevaga jõustumine",
    "Eeldatava kuupäevaga jõustumine",
    "Üldises korras",
    "Teadmata",
)
#: The kinds `save_topic` refuses without a `kuupaev` (and the only ones that keep one).
C_LIIGID_WITH_DATE: Final = ("Kindla kuupäevaga jõustumine", "Eeldatava kuupäevaga jõustumine")

# ---------------------------------------------------------------------------
# logic.ts:185-231 — the opinion status, as value classes
# ---------------------------------------------------------------------------

#: One example of every class of `valja` Lovable distinguishes, with what
#: Lovable means by it: ``("SENT", day)``, ``("NOT_SENT", None)`` or
#: ``("DRAFTING", None)`` («koostamisel»: anything else, blank included).
OPINION_STATUS_FORMS: Final[dict[str, tuple[str, tuple[str, str | None]]]] = {
    "iso": ("2026-03-12", ("SENT", "2026-03-12")),
    "d.m.yyyy": ("12.3.2026", ("SENT", "2026-03-12")),
    "dd.mm.yyyy": ("12.03.2026", ("SENT", "2026-03-12")),
    "d.m.yy": ("12.3.26", ("SENT", "2026-03-12")),
    "d/m/yyyy": ("12/03/2026", ("SENT", "2026-03-12")),
    "d-m-yyyy": ("12-03-2026", ("SENT", "2026-03-12")),
    "ei saatnud": ("ei saatnud", ("NOT_SENT", None)),
    "EI SAATNUD.": ("EI SAATNUD.", ("NOT_SENT", None)),
    # In Lovable's own classifier fixture; `isEiSaatnud` is a substring test.
    "ei saatnud arvamust": ("ei saatnud arvamust", ("NOT_SENT", None)),
    "free text": ("saadetakse hiljem", ("DRAFTING", None)),
    "blank": ("", ("DRAFTING", None)),
}

# ---------------------------------------------------------------------------
# What Juristid decided (the oracle's and LC-V1's table)
# ---------------------------------------------------------------------------

MAPPED: Final = "MAPPED"
HELD: Final = "HELD"
PENDING: Final = "PENDING_OWNER_DECISION"

#: (vocabulary, value) → (treatment, detail). ``detail`` is the Juristid key a
#: MAPPED value becomes, the decision a HELD value cites, or the finding a
#: PENDING value waits on. Every value above appears exactly once.
TREATMENT: Final[dict[tuple[str, str], tuple[str, str]]] = {
    # Legal acts: the eight classes map 1:1 (audit §A.1 A1, ADR 0148 §3).
    ("oigusakt", "VTK"): (MAPPED, "vtk"),
    ("oigusakt", "seadus"): (MAPPED, "seadus"),
    ("oigusakt", "määrus"): (MAPPED, "maarus"),
    ("oigusakt", "muu siseriiklik"): (MAPPED, "muu-siseriiklik"),
    ("oigusakt", "ELi konsultatsioon"): (MAPPED, "eli-konsultatsioon"),
    ("oigusakt", "ELi direktiiv"): (MAPPED, "direktiiv"),
    ("oigusakt", "ELi määrus"): (MAPPED, "el-maarus"),
    ("oigusakt", "muu ELi dokument"): (MAPPED, "muu-eli-dokument"),
    ("oigusakt", "Muu"): (PENDING, "LV-01: map «Muu» to muu-siseriiklik, a neutral row, or hold"),
    # Stages: the eleven form values map exactly (audit §A.1 A3, ADR 0202 §3).
    ("hetkeseis", "idee"): (MAPPED, "idea"),
    ("hetkeseis", "kooskõlastusringil"): (MAPPED, "consultation"),
    ("hetkeseis", "valitsuses"): (MAPPED, "government"),
    ("hetkeseis", "Riigikogus"): (MAPPED, "parliament"),
    ("hetkeseis", "jõustumise ootel"): (MAPPED, "awaiting_entry"),
    ("hetkeseis", "jõustunud"): (MAPPED, "in_force"),
    ("hetkeseis", "Eesti seisukoht koostamisel"): (MAPPED, "estonian_eu_position"),
    ("hetkeseis", "ELi menetluses"): (MAPPED, "eu_procedure"),
    ("hetkeseis", "ELi õiguse ülevõtmise ootel"): (MAPPED, "awaiting_transposition"),
    ("hetkeseis", "rohkem ei tegele"): (MAPPED, "monitoring_stopped"),
    ("hetkeseis", "muu"): (MAPPED, "other"),
    # Commencements (approval 11.11 §1 «Commencement dates»; audit §A.1 A7/A8).
    ("c_liik", "Kindla kuupäevaga jõustumine"): (MAPPED, "KNOWN_DATE|PLANNED_COMMENCEMENT"),
    ("c_liik", "Eeldatava kuupäevaga jõustumine"): (MAPPED, "ESTIMATED_COMMENCEMENT"),
    ("c_liik", "Üldises korras"): (MAPPED, "GENERAL_ORDER"),
    ("c_liik", "Teadmata"): (
        PENDING,
        "LV-02: skip «Teadmata» (it asserts no date) or hold only that row",
    ),
    # Opinion status (approval 11.11 §1 «Opinion requests»; audit §A.1 A11).
    ("valja", "iso"): (MAPPED, "SENT"),
    ("valja", "d.m.yyyy"): (MAPPED, "SENT"),
    ("valja", "dd.mm.yyyy"): (MAPPED, "SENT"),
    ("valja", "d.m.yy"): (PENDING, "LV-03: Lovable reads d.m.yy as sent"),
    ("valja", "d/m/yyyy"): (PENDING, "LV-03: Lovable reads d/m/yyyy as sent"),
    ("valja", "d-m-yyyy"): (PENDING, "LV-03: Lovable reads d-m-yyyy as sent"),
    ("valja", "ei saatnud"): (MAPPED, "NOT_SENT"),
    ("valja", "EI SAATNUD."): (MAPPED, "NOT_SENT"),
    ("valja", "ei saatnud arvamust"): (PENDING, "LV-03: Lovable's «ei saatnud» is a substring"),
    # Free text is neither a day nor «ei saatnud»: Juristid holds rather than
    # guesses (approval 11.11 §1: «no document … is invented»; audit A11).
    ("valja", "free text"): (HELD, "approval 11.11 §1: nothing is invented"),
    ("valja", "blank"): (HELD, "approval 11.11 §1: a blank never ends a request"),
    # OneNote links (audit §A.1 A13).
    ("onenote_scheme", "https"): (MAPPED, "MatterSourceReference"),
    ("onenote_scheme", "http"): (HELD, "ADR 0192: only an https:// link is a source link"),
    ("onenote_scheme", "onenote"): (PENDING, "LV-03b: a desktop onenote: link is held today"),
}


def values_of(vocabulary: str) -> tuple[str, ...]:
    """Every value Lovable can write for one vocabulary."""
    return {
        "oigusakt": OIGUSAKTID,
        "hetkeseis": FORM_HETKESEISUD,
        "c_liik": C_LIIGID,
        "valja": tuple(OPINION_STATUS_FORMS),
        "onenote_scheme": ONENOTE_LINK_SCHEMES,
    }[vocabulary]


VOCABULARIES: Final = ("oigusakt", "hetkeseis", "c_liik", "valja", "onenote_scheme")
