"""Settings entry point for static analysis only.

`mypy` with `django-stubs` imports the settings module to build its model
metadata. The real settings module deliberately refuses to load without a
`DJANGO_SECRET_KEY`, which is the behaviour we want at runtime and not something
to weaken. This shim supplies a throwaway value for the type checker and then
re-exports the real settings unchanged.

Never point a running process at this module.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "static-analysis-only-never-served")
os.environ.setdefault("DJANGO_DEBUG", "0")

from config.settings import *
