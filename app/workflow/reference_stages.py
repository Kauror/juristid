"""The reviewed `Hetkeseis` vocabulary, as code.

Reference data, not a fixture, and the same shape `app.taxonomy.reference_data`
gives Valdkonnad: one manifest a reviewer reads, a frozen copy inside each
migration that changes the database, and a test holding the two to each other.

Deliberately *not* ``app.workflow.vocabulary``. That module maps the historical
workbook's raw ``HETKESEIS`` spellings onto canonical keys and must keep saying
what the workbook said in 2011; this one says what a lawyer is offered today.
The two answer different questions about the same column and only one of them
may change when the department rewords a label — which is precisely what
version 2.0 below does.

Version 1.0 — read out of the workbook
--------------------------------------

``workflow/0004`` seeded ten stages, taken from the live
``Tööd eelnõudega.xlsx`` vocabulary, and marked every one of them
``is_provisional``: the list matched the workbook, but the wording, the help
text and the track applicability were still the department's call.
``workflow/0006`` then replaced the seeded one-line glosses with the
department's own descriptions, supplied 2026-08-25.

Version 2.0 — reviewed by the lawyers
-------------------------------------

The second structured feedback round on the demo, 2026-09-17, reviewed the
vocabulary itself. Three things came out of it and each is a different kind of
change:

**Three labels are reworded, and nothing else about them moves.** ``Ootan
jõustumist``, ``Eesti seisukoht`` and ``Ootan ELi õiguse ülevõtmist`` become
``Jõustumise ootel``, ``Eesti seisukoht koostamisel`` and ``ELi õiguse
ülevõtmise ootel``. The keys, the rows, the ``Matter.stage`` relations, the
help texts, the sort order and the register filters are all untouched — a stage
is addressed by its key everywhere it is stored, filtered or reported, so a
reword is a display change and nothing else. Two of the three also stop writing
in the first person: a column that says *I am waiting* is a sentence about
whoever is reading it rather than about where the file stands.

**One stage is new: ``Rohkem ei tegele``.** The workbook has carried the raw
value ``rohkem pole tegevusi plaanis`` since 2011, and ``workflow/0004``
deliberately read it as the ``MONITORING_STOPPED`` *disposition* rather than as
a stage, because it says Koda stopped working on the file and not where the
external process stands. That reading of the **historical column** is correct
and is not touched here. What the lawyers asked for is different: a Hetkeseis
they can choose while the file stays open, for the ordinary case where the
external process is still running somewhere and this office has decided not to
follow it any further.

So stage and disposition stay separate, which is the product's own rule
(AGENTS.md, master specification 3.4). Choosing this stage records where Koda's
attention is; it does **not** close the Matter, does not archive it, does not
call `Lõpeta teema` and writes no `Disposition`. The stage's own help text says
so, because a lawyer reading the label alone would reasonably assume otherwise.

**Nothing is retired.** Every version-1.0 key is in version 2.0, which is why
this module carries no ``RETIRED_STAGE_KEYS``. The mechanism exists and works —
``app.workflow.selectors.stages_including`` and docs/adr/0032 §Amendment — and
this round simply has no use for it.

**Nothing is remapped.** In particular no Matter is moved onto the new stage.
The Matters whose historical ``rohkem pole tegevusi plaanis`` row was read as a
disposition keep that reading; inferring the new stage for them would be
rewriting a decade of somebody else's filing on a coincidence of wording, and it
would also be untrue, because a disposition and a stage are not the same claim.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Bumped when the *set* or the *wording* of the offered stages changes. Pinned
#: by `tests/test_reference_stages.py`, so growing or rewording the vocabulary
#: is a decision somebody made rather than a diff that slipped through.
REFERENCE_STAGE_VERSION = "2.0"

#: Where version 1.0 came from, and when.
STAGE_SOURCE_TITLE = "Tööd eelnõudega.xlsx — veerg HETKESEIS"
STAGE_SOURCE_PUBLISHER = "Eesti Kaubandus-Tööstuskoda, õigusosakond"
STAGE_SOURCE_VERIFIED_ON = "2026-08-25"

#: Version 2.0's own provenance, for the reason `app.taxonomy.reference_data`
#: gives each of its withdrawals one: the reviewer's question is not "where did
#: this list come from" but "who reworded those three labels, and when".
STAGE_REVIEW_SOURCE_TITLE = "Koda Õigusloome — juristide tagasiside, Hetkeseis ja Õigusakt"
STAGE_REVIEW_VERIFIED_ON = "2026-09-17"


@dataclass(frozen=True)
class ReferenceStage:
    key: str
    label_et: str
    sort_order: int


#: Version 1.0 — the ten stages `workflow/0004` seeded, under the labels it
#: seeded them with. Kept rather than overwritten: a reader asking what «Ootan
#: jõustumist» was must still be able to find out, and the migration that
#: rewords it needs the old wording in order to refuse a row somebody else has
#: already edited.
REFERENCE_STAGES_V1: tuple[ReferenceStage, ...] = (
    ReferenceStage(key="idea", label_et="Idee", sort_order=10),
    ReferenceStage(key="consultation", label_et="Kooskõlastusringil", sort_order=20),
    ReferenceStage(key="government", label_et="Valitsuses", sort_order=30),
    ReferenceStage(key="parliament", label_et="Riigikogus", sort_order=40),
    ReferenceStage(key="awaiting_entry", label_et="Ootan jõustumist", sort_order=50),
    ReferenceStage(key="in_force", label_et="Jõustunud", sort_order=60),
    ReferenceStage(key="estonian_eu_position", label_et="Eesti seisukoht", sort_order=70),
    ReferenceStage(key="eu_procedure", label_et="ELi menetluses", sort_order=80),
    ReferenceStage(
        key="awaiting_transposition", label_et="Ootan ELi õiguse ülevõtmist", sort_order=90
    ),
    ReferenceStage(key="other", label_et="Muu", sort_order=100),
)

#: The three version-1.0 labels the lawyers reworded, as ``key -> new label``.
#:
#: Kept as data because two places have to agree about them — the migration that
#: applies the rewording and the test that proves no key, row or relation moved
#: with it. The *old* label is not restated here: it is above, in version 1.0,
#: and a second copy is a second place for a diacritic to go missing.
REWORDED_STAGE_LABELS_V2: dict[str, str] = {
    "awaiting_entry": "Jõustumise ootel",
    "estonian_eu_position": "Eesti seisukoht koostamisel",
    "awaiting_transposition": "ELi õiguse ülevõtmise ootel",
}

#: The one stage version 2.0 adds, with the sentence that keeps it from being
#: read as a closure.
#:
#: ``sort_order`` 95 puts it between ``awaiting_transposition`` (90) and ``Muu``
#: (100), which is where the reviewed list has it and which keeps ``Muu`` last
#: without renumbering anything.
NEW_STAGE_KEY_V2 = "no_further_work"
NEW_STAGE_LABEL_V2 = "Rohkem ei tegele"
NEW_STAGE_SORT_ORDER_V2 = 95
NEW_STAGE_HELP_V2 = (
    "Koda ei kavatse selle teemaga enam aktiivselt tegeleda. See on hetkeseis, "
    "mitte teema lõpetamine: teema jääb avatuks ja selle sulgemine on eraldi "
    "otsus («Lõpeta teema»)."
)

#: Version 2.0 — the eleven stages offered today, in reviewed order.
#:
#: Derived from version 1.0 rather than retyped, for the reason
#: `app.taxonomy.reference_data` derives each of its versions by exclusion: the
#: transcription of what the department wrote down is above and is not copied a
#: second time, so a comma cannot go missing in the copy.
REFERENCE_STAGES_V2: tuple[ReferenceStage, ...] = tuple(
    sorted(
        (
            *(
                ReferenceStage(
                    key=stage.key,
                    label_et=REWORDED_STAGE_LABELS_V2.get(stage.key, stage.label_et),
                    sort_order=stage.sort_order,
                )
                for stage in REFERENCE_STAGES_V1
            ),
            ReferenceStage(
                key=NEW_STAGE_KEY_V2,
                label_et=NEW_STAGE_LABEL_V2,
                sort_order=NEW_STAGE_SORT_ORDER_V2,
            ),
        ),
        key=lambda stage: (stage.sort_order, stage.label_et),
    )
)

#: The name the rest of the codebase imports. No stage was retired, so this is
#: every row the vocabulary has.
REFERENCE_STAGES: tuple[ReferenceStage, ...] = REFERENCE_STAGES_V2

#: The stable keys, in reviewed order.
REFERENCE_STAGE_KEYS: tuple[str, ...] = tuple(stage.key for stage in REFERENCE_STAGES)
