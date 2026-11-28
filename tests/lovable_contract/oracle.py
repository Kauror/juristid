"""The independent oracle now lives in the application (`app.legacy_import.parity.oracle`) so the
repeatable parity verifier can use it; this alias keeps every contract test importing it from here.
"""

import sys

from app.legacy_import.parity import oracle as _moved

sys.modules[__name__] = _moved
