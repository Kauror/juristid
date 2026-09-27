"""Negative tests name the refusal they exercise (ENG-108).

``pytest.raises(DomainError)`` passes on *any* business refusal. A test written
to prove "a closed Teema refuses this write" also passed when the write was
refused because a title was missing, a date was malformed, or an unrelated rule
had moved in front of the one under test — the assertion held while the
behaviour it named had gone. `DomainError` carries no machine-readable code; its
message is the refusal a person reads, so that is what a test names.

Pass the service's own constant where there is one, so a reworded refusal does
not break a test that still means the same thing.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.core.errors import DomainError


def refused(message: str) -> Any:
    """``pytest.raises(DomainError)`` that also requires this refusal's words."""
    return pytest.raises(DomainError, match=re.escape(message))
