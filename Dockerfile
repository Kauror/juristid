# syntax=docker/dockerfile:1

# Both stages are Debian 13 (trixie), and both are pinned by digest. The tag is
# for the reader; Docker pulls the digest, so a rebuild cannot quietly move to a
# different base, and moving to a newer one is a reviewed change to this file.
# Debian 12 (bookworm) was left in 2026-10 because it no longer had a fix for
# most of what it shipped (JSEC-007).
#
# The digests are the multi-platform index digests, so `--platform linux/amd64`
# in release-image.yml still selects the amd64 image from them. To move a base
# forward, resolve the tag's current index digest and replace it here:
#
#   docker buildx imagetools inspect python:3.13-slim-trixie
#   docker buildx imagetools inspect ghcr.io/astral-sh/uv:python3.13-trixie-slim
#
# (the `Digest:` line). The two stages must stay on one Python minor and one
# Debian release: the virtual environment is built in the first and copied
# into the second, and points at the interpreter it was built with.

# ---------------------------------------------------------------------------
# Build stage: resolve the locked dependency set into a self-contained venv.
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim@sha256:7dd72af08bdef012fdf433082190d2b5133a96b6a43aca0bb71c2aa793e47e20 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src

# Dependencies first so application edits do not invalidate the layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
# uv leaves an empty, world-writable `.lock` in the environment it manages.
# Nothing in the runtime image runs uv, and a world-writable file in a tree the
# application user must not write is the one exception it would otherwise have.
RUN uv sync --frozen --no-dev && rm -f /opt/venv/.lock

# Static assets are baked into the image. DJANGO_STATIC_MANIFEST is forced on
# so the hashed manifest exists for the runtime process, which runs with
# DJANGO_DEBUG off and therefore expects it.
RUN DJANGO_DEBUG=1 DJANGO_STATIC_MANIFEST=1     /opt/venv/bin/python manage.py collectstatic --noinput

# ---------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------
FROM python:3.13-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings

# Tesseract and its Estonian and English language data. A system package rather
# than a wheel, installed here and in CI from the same list — an OCR engine that
# exists on one developer's machine and not in production is how "extraction
# works" becomes an environment-dependent claim (Stage-2B brief 88, 89).
#
# est+eng because the corpus is Estonian and its annexes are routinely English.
# Tesseract asked for a language it does not have falls back silently and returns
# confident nonsense, which is why the application probes for these at runtime
# instead of assuming them.
RUN apt-get update \
    && apt-get install --no-install-recommends --yes \
        tesseract-ocr \
        tesseract-ocr-est \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 juristid \
    && useradd --system --uid 10001 --gid juristid --create-home juristid

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# The base image's own installer, gone. pip, setuptools and wheel are build
# tools and nothing here builds: the application runs from /opt/venv, which uv
# made without them, and no start-up command or `manage.py` command installs
# anything. Left in, they were the only Python packages in the image outside
# the locked set — pip carried vendored urllib3, msgpack and setuptools with
# advisories of their own (JSEC-007) — and an installer is a way to add code to
# a running container. The *system* interpreter is named because `python` on
# PATH is now the venv's, which never had them; the check after it fails the
# build if any of the three is still importable there.
RUN /usr/local/bin/python3 -m pip uninstall --yes --no-input pip setuptools wheel \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13 /usr/local/bin/wheel \
    && /usr/local/bin/python3 -c "import importlib.util, sys; left = [m for m in ('pip', 'setuptools', 'wheel') if importlib.util.find_spec(m)]; sys.exit(f'still installed: {left}' if left else 0)"

# The code is root's. `COPY` writes it as root and nothing below hands it over:
# the process that serves requests is the one an attacker gets, and a process
# that can rewrite the code its next worker imports turns a file-write bug into
# code execution that survives a restart (JOPS-006).
COPY --from=builder /src /app

# When this image was built. Recorded here rather than passed in as an
# environment variable, because a stamp somebody has to remember to set is a
# stamp that eventually describes a different build than the one running.
#
# The layer sits directly after the source copy, so any application change
# invalidates it and produces a fresh time. A rebuild that changes nothing
# keeps the old stamp, which is correct: it is the same image.
RUN date -u +"%Y-%m-%dT%H:%M:%SZ" > /app/BUILD_STAMP

# The exact commit this image was built from.
#
# Three different facts get called "the version" and only one of them answers
# "what code is this": the source revision. BUILD_STAMP above says *when*, an
# image tag says which name somebody gave it, and neither survives being wrong.
#
# Passed in because `.git` is excluded from the build context on purpose — the
# production Compose file builds from a checkout on the server, and copying the
# repository history into the image would be both large and pointless. The
# deployment script supplies it from the reviewed target SHA it already
# verified, so the value cannot drift from the code beside it; supply it by
# hand and it is exactly as reliable as the hand.
#
# Empty when nobody passed one. `manage.py deployment_readiness` refuses a
# real-data deployment that does not know its own commit, which is what stops
# an unset build arg from turning into an image nobody can identify later.
ARG GIT_SHA=""
RUN printf '%s' "$GIT_SHA" > /app/GIT_SHA

# Evidence and derivatives are separate directories because they are separate
# storage classes: one must survive and be backed up, the other may be deleted
# and rebuilt. Both must exist and belong to the application user, because a
# named volume inherits the ownership of the image path it shadows. The OneNote
# source root is the third storage class (config/settings.py): a bind mount in
# every deployed stack, created here because the development stack does not
# mount it and the application can no longer create it under a root-owned /app.
#
# `pending-uploads` is on this line for that second reason alone. It is neither
# canonical nor rebuildable — it is one person's unfinished form — but the
# production stack now mounts a named volume over it so that `web` and
# `intake-reader` see the same staged bytes (deploy/unraid-main/compose.yml,
# docs/adr/0064, docs/adr/0072). Docker initialises an empty named volume from the image path
# it covers, so a path that did not exist here would produce a root-owned mount
# point, and `web` runs as `juristid` and could not stage a file into it at all.
#
# These four are the only paths under /app the application user owns. Anything
# else the application writes goes to a mount, to /tmp (worker heartbeats,
# gunicorn's scratch files) or to the user's home (gunicorn's control socket).
# A command left to write a default output path inside /app —
# `import_legacy_register` without `--report-dir`, `check_era_contracts`
# without `--check` — now stops with a permission error instead of writing into
# a container that is about to be discarded (deploy/unraid-main/README.md).
RUN mkdir -p /app/evidence /app/derivatives /app/legacy-source /app/pending-uploads \
    && chown -R juristid:juristid /app/evidence /app/derivatives /app/legacy-source /app/pending-uploads

USER juristid

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# Migrations are applied as a controlled deployment step, not on container
# start (master specification 24.2).
#
# Three workers and a 120-second timeout for six users. Not tuned for
# throughput: the longest request this application serves is an evidence upload
# of up to MAX_EVIDENCE_UPLOAD_BYTES over a tunnel, and a worker killed
# mid-upload loses the file rather than the second it was short by. The
# production Compose command repeats these values instead of inheriting them
# because it also turns access logging on, and a command that overrides one
# thing overrides all of them — a test keeps the two from drifting apart
# (tests/test_deployment_image.py).
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
