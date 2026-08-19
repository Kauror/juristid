"""The reviewed `Hetkeseis` vocabulary, as code.

Migration ``workflow/0004_seed_stage_vocabulary`` seeds these values into the
database and, as every Django migration must, carries its own frozen copy of
them — a migration that imported live constants would change meaning whenever
the constants did, which is the one thing a migration may never do.

This module exists because the importer needs the same list *without* a
database: the offline inspector runs on a machine with no PostgreSQL and still
has to tell a reviewer which of the workbook's status labels are known. The two
copies are kept honest by ``tests/test_workflow_vocabulary.py``, which fails if
they drift apart.
"""

from __future__ import annotations

#: Raw workbook label to canonical stage key. Ten of the eleven values.
RAW_LABEL_TO_STAGE: dict[str, str] = {
    "idee": "idea",
    "kooskõlastusringil": "consultation",
    "valitsuses": "government",
    "Riigikogus": "parliament",
    "ootan jõustumist": "awaiting_entry",
    "jõustunud": "in_force",
    "Eesti seisukoht": "estonian_eu_position",
    "ELi menetluses": "eu_procedure",
    "ootan ELi õiguse ülevõtmist": "awaiting_transposition",
    "muu": "other",
}

#: The eleventh. It describes Koda stopping work, which is a closure reason and
#: not a place the external process has reached.
RAW_LABEL_TO_DISPOSITION: dict[str, str] = {
    "rohkem pole tegevusi plaanis": "MONITORING_STOPPED",
}

#: Every label the reviewed vocabulary knows.
CONTROLLED_LABELS: frozenset[str] = frozenset(RAW_LABEL_TO_STAGE) | frozenset(
    RAW_LABEL_TO_DISPOSITION
)


def is_known_label(label: str) -> bool:
    return label in CONTROLLED_LABELS
