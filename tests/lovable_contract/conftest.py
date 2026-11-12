"""Fixtures of the contract suite: the synthetic world, built per test."""

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
