"""The Lovable↔Juristid contract suite (QA audit 2026-11-11 §D.2).

`fake.py` (the in-process Lovable), `vocabulary.py` (Lovable's vocabulary at a
named commit), `world.py` (the synthetic two-sided world and the real run) and
`oracle.py` (an independent re-statement of the approved contract). The
`test_*` modules hold one or more tests per stable contract ID (`LC-…`); each
test's docstring names its ID and the contract sentence it asserts.
"""
