"""The reviewed `Hetkeseis` vocabulary, as code.

Reference data, not a fixture, and the same shape `app.taxonomy.reference_data`
gives Valdkonnad: one manifest a reviewer reads, a frozen copy inside each
migration that changes the database, and a test holding the two to each other.

Deliberately *not* ``app.workflow.vocabulary``. That module maps the historical
workbook's raw ``HETKESEIS`` spellings onto canonical keys and must keep saying
what the workbook said in 2011; this one says what a lawyer is offered today.
The two answer different questions about the same column and only one of them
may change when the department rewords a label — which is exactly what version
2.0 below does.

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
vocabulary itself. What came out of it is **three rewordings and nothing else**.

``Ootan jõustumist``, ``Eesti seisukoht`` and ``Ootan ELi õiguse ülevõtmist``
become ``Jõustumise ootel``, ``Eesti seisukoht koostamisel`` and ``ELi õiguse
ülevõtmise ootel``. The keys, the rows, the ``Matter.stage`` relations, the help
texts, the sort orders and the register filters are all untouched — a stage is
addressed by its key everywhere it is stored, filtered or reported, so a reword
is a display change and nothing else. Two of the three also stop writing in the
first person: a column that says *I am waiting* is a sentence about whoever is
reading it rather than about where the file stands.

**Nothing is retired.** Every version-1.0 key is in version 2.0, which is why
this module carries no ``RETIRED_STAGE_KEYS``. The mechanism exists and works —
``app.workflow.selectors.stages_including`` and docs/adr/0032 §Amendment — and
this round simply has no use for it.

Why ``Rohkem ei tegele`` is not a stage
---------------------------------------

The feedback asked for it as a Hetkeseis, and it is not one.

``Hetkeseis`` says where the **external** process stands: the Riigikogu has it,
it is on a consultation round, the act is waiting to come into force. *Koda has
stopped working on this* is a different question about a different actor, and
the product already answers it — ``Disposition.MONITORING_STOPPED``, «Koda
lõpetas jälgimise», offered on ``Lõpeta teema`` as «Koda ei tegele edasi» and in
the composer as «Loobuti». ADR 0032 separated the two deliberately, and adding a
stage that means the second would put both answers in one column and leave every
surface reading it unable to tell which had been given.

The workbook agrees, and has since 2011. Its raw value ``rohkem pole tegevusi
plaanis`` is read by ``workflow/0004`` as that disposition rather than as a
stage, for exactly this reason. A stage with neighbouring words would have made
the historical reading and the current vocabulary disagree about the same words.

**Nothing is remapped either.** No Matter is moved and no historical mapping is
re-pointed; the concept was already implemented and stays where it was.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Bumped when the *set* or the *wording* of the offered stages changes. Pinned
#: by `tests/test_reference_stages.py`, so rewording the vocabulary is a
#: decision somebody made rather than a diff that slipped through.
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

#: Version 2.0 — the ten stages offered today, in reviewed order.
#:
#: Derived from version 1.0 rather than retyped, for the reason
#: `app.taxonomy.reference_data` derives each of its versions by exclusion: the
#: transcription of what the department wrote down is above and is not copied a
#: second time, so a comma cannot go missing in the copy.
REFERENCE_STAGES_V2: tuple[ReferenceStage, ...] = tuple(
    ReferenceStage(
        key=stage.key,
        label_et=REWORDED_STAGE_LABELS_V2.get(stage.key, stage.label_et),
        sort_order=stage.sort_order,
    )
    for stage in REFERENCE_STAGES_V1
)

#: The name the rest of the codebase imports. No stage was retired and none was
#: added, so this is every row the vocabulary has.
REFERENCE_STAGES: tuple[ReferenceStage, ...] = REFERENCE_STAGES_V2

#: The stable keys, in reviewed order.
REFERENCE_STAGE_KEYS: tuple[str, ...] = tuple(stage.key for stage in REFERENCE_STAGES)
