# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Build stage: resolve the locked dependency set into a self-contained venv.
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src

# Dependencies first so application edits do not invalidate the layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# Static assets are baked into the image. DJANGO_STATIC_MANIFEST is forced on
# so the hashed manifest exists for the runtime process, which runs with
# DJANGO_DEBUG off and therefore expects it.
RUN DJANGO_DEBUG=1 DJANGO_STATIC_MANIFEST=1     /opt/venv/bin/python manage.py collectstatic --noinput

# ---------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings

RUN groupadd --system --gid 10001 juristid \
    && useradd --system --uid 10001 --gid juristid --create-home juristid

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /src /app

# A named volume inherits the ownership of the image path it shadows, so the
# evidence directory must exist and belong to the application user.
RUN mkdir -p /app/evidence && chown -R juristid:juristid /app

USER juristid

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# Migrations are applied as a controlled deployment step, not on container
# start (master specification 24.2).
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]
