"""Moved to `app.legacy_import.parity.vocabulary` (see `oracle.py` here)."""

import sys

from app.legacy_import.parity import vocabulary as _moved

sys.modules[__name__] = _moved
