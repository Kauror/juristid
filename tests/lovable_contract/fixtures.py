"""Fixtures of the contract suite: the synthetic world, built per test.

Imported by each test module rather than kept in a `conftest.py`: a directory
conftest is not applied when pytest is given this directory's files out of
order with others (the FAST job's file list did exactly that), and `world`
would then silently resolve to `tests/conftest.py`'s fixture of that name. A
fixture imported into a module overrides that one for the module."""

from __future__ import annotations

import pytest


@pytest.fixture
def pilot_on(settings):
    """The importer and the synchronisation may write (the container flags)."""
    settings.LOVABLE_PILOT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


@pytest.fixture
def world(db, pilot_on):
    """Three converged register Teemad, 2099_1..3, in APPLY."""
    from tests.lovable_contract.world import build_world

    return build_world(3)
