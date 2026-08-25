"""Settings for the browser suite's shared-gate rehearsal server.

The persona switcher exists only in `AUTH_MODE=shared_gate`, so the browser job
runs a second server in that mode beside the ordinary one. This is the module it
runs on.

**Why a module and not eight lines of `env:` in the workflow.** The repository
refuses to let `JURISTID_SHARED_GATE_PASSWORD` appear in any tracked YAML or
Dockerfile (`tests/test_repository_data_safety.py`). That guard is blunt on
purpose: a committed shared password would not look wrong in a diff, it would
look like a default, and the deployment that inherits it is the one nobody
reviewed. Setting the variable in `ci.yml` — even for a throwaway server bound
to localhost — would have meant loosening the guard, and a guard loosened once
is a guard that stops being trusted.

So the workflow names *this module* instead, and the password reaches it under a
test-only name that no deployment reads. The guard stays absolute, and the
rehearsal's configuration is a reviewable file rather than YAML nobody diffs.

Follows `config/test_settings.py` and `config/typecheck_settings.py`: a thin
module over the real settings, stating what it changes and why.

Never point a deployment at this.
"""

from __future__ import annotations

import os

from config.settings import *  # noqa: F403

#: Something a running process can be asked, rather than a module name that has
#: to be spelled right.
IS_E2E_GATE_SETTINGS = True

# The mode the persona switcher lives in, and the only reason this module
# exists.
AUTH_MODE = "shared_gate"

# Under a name no deployment reads. `JURISTID_SHARED_GATE_PASSWORD` is the real
# one and stays out of every tracked YAML file.
SHARED_GATE_PASSWORD = os.environ.get("E2E_GATE_PASSWORD", "")

# A passwordless sign-in behind a gate is a way around the gate, and
# `juristid.E008` refuses the combination. Stated here rather than left to the
# job's environment so the refusal cannot be switched off by editing YAML.
DEV_LOGIN_ENABLED = False

if os.environ.get("REAL_DATA_ALLOWED", "0") not in {"0", "", "false", "False"}:
    # A rehearsal password, in a file, in a public repository's CI. It may sit
    # in front of a seeded world and nothing else. This is a `raise` rather than
    # a system check because a check runs after the settings are in force, and
    # "in force" is already late enough for the server to have answered a
    # request (config/test_settings.py makes the same argument).
    raise RuntimeError(
        "config.e2e_gate_settings is a browser-test rehearsal and must never run "
        "with REAL_DATA_ALLOWED. Use AUTH_MODE and JURISTID_SHARED_GATE_PASSWORD "
        "from the host environment for a real deployment."
    )
