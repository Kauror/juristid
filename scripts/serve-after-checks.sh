#!/bin/sh
# Serve only a configuration the system checks accept (JOPS-004).
#
# Every `manage.py` command runs Django's system checks before it does
# anything, so a worker started as `python manage.py run_…` already refuses a
# configuration that `juristid.E0xx` refuses. gunicorn does not: it imports
# `config.wsgi`, and WSGI start-up runs no checks. With REAL_DATA_ALLOWED=1 and
# AUTH_MODE=none `manage.py check` failed on juristid.E006 while the web
# container answered /healthz with 200 and served pages — a misconfiguration
# that looked healthy.
#
# So the web process is started through this script: the checks first, at the
# ERROR level and with `--deploy`, and only if they pass is the real command
# exec'd in this shell's place (so it is still PID 1 and still gets the
# signals). A refused configuration exits non-zero before anything listens;
# Compose restarts it, `up --wait` fails, and the container never reports
# healthy. Warnings are printed and do not stop it.
#
# Usage, as the image's CMD and the deployed Compose `command:` spell it:
#   sh /app/scripts/serve-after-checks.sh gunicorn config.wsgi:application …
#
# tests/test_deployment_image.py holds the image and both stacks to this.
set -eu

python manage.py check --deploy --fail-level ERROR

exec "$@"
