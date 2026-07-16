# Synapse backend Docker image (Python 3.11+ slim, non-root, healthcheck)
# Built context: backend/
# Image: synapse-backend:latest
# Build args: APP_VERSION (e.g. "1.2.0", default "dev")
#
# 1.9.1 W4 (SEC-DEPS-1): this file now declares TWO independent stages:
#
#   1. "production" (NEW, additional target — `docker build --target production`).
#      Prod-only deps from the PINNED lockfile (requirements-prod-lock.txt, generated via
#      `uv pip compile pyproject.toml -o requirements-prod-lock.txt`); no tests/, no dev
#      tooling (ruff/black/mypy/pytest). Smaller image, smaller attack surface. NOT wired
#      into any existing build path yet — desktop-release.yml / GHCR publishing can opt in
#      to `--target production` in a follow-up without any code change here.
#
#   2. "default" (UNCHANGED — this is the stage docker builds when no --target is given,
#      because it is declared LAST in this file). This is EXACTLY the pre-1.9.1 image:
#      editable install with dev/test deps, tests/ copied in (CI still runs pytest inside
#      this image — see backend/.dockerignore comment from 1.9.0 W1). Every existing
#      deployment (TrueNAS custom-app YAML pinned to :latest, docker-compose.yml, CI) keeps
#      building/pulling this stage with ZERO behavior change (NON-REGRESSIONE).
#
# Both stages install `postgresql-client` (SEC-OPS-2, 1.9.1 W4): the new scheduled/
# pre-update `pg_dump` backup (app/ops/backup.py) needs the `pg_dump` binary on PATH in
# whichever image is actually running — that is the "default" stage today, so it must not
# be skipped there.

# ─────────────────────────────────────────────────────────────────────────────
# Stage: production (NEW, 1.9.1 W4 / SEC-DEPS-1) — additional target, opt-in only.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS production

ARG APP_VERSION=dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_VERSION=${APP_VERSION}

WORKDIR /app

# gcc: build backend for any C-extension wheels without prebuilt manylinux wheels.
# postgresql-client: `pg_dump` binary for app/ops/backup.py (SEC-OPS-2).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install PINNED production-only dependencies (no dev/test deps) from the lockfile —
# `--no-deps` because the lockfile already contains the fully resolved transitive graph;
# re-resolving here would defeat the point of pinning (SEC-DEPS-1).
COPY requirements-prod-lock.txt pyproject.toml ./
RUN pip install --no-deps -r requirements-prod-lock.txt

# Copy ONLY production application code — no tests/, no dev tooling.
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY scripts/ ./scripts/

# Install the local package itself without re-resolving deps (already pinned above).
# NOTE: no --no-build-isolation — hatchling (the PEP 517 build backend) is intentionally
# NOT in requirements-prod-lock.txt (it is a build-time tool, not a runtime dependency), so
# pip needs its normal isolated build env to fetch it for this one step.
RUN pip install --no-deps .

# Create non-root user
RUN useradd -m -u 1000 synapse && chown -R synapse:synapse /app
USER synapse

# ─── F17 CLI Provider: Claude Code OAuth subscription auth (ADR-0042) ───
RUN mkdir -p /home/synapse/.claude && \
    chown -R synapse:synapse /home/synapse/.claude && \
    chmod 700 /home/synapse/.claude
ENV CLAUDE_CONFIG_DIR=/home/synapse/.claude

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/status')" || exit 1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ─────────────────────────────────────────────────────────────────────────────
# Stage: default (UNCHANGED behavior — the stage built when no --target is given, because
# it is the LAST stage in the file). Every existing build path (docker-compose.yml, CI
# `docker build` validation, TrueNAS custom-app :latest pulls) is unaffected.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS default

# Build argument: version tag (passed via --build-arg APP_VERSION=...)
ARG APP_VERSION=dev

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_VERSION=${APP_VERSION}

WORKDIR /app

# Install system dependencies.
# postgresql-client (1.9.1 W4, SEC-OPS-2): `pg_dump` binary for the new scheduled/
# pre-update backup op (app/ops/backup.py) — this IS the image real deployments run today,
# so the binary must live here, not only in the (currently unused) "production" stage above.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from pyproject.toml (including dev/test deps)
COPY pyproject.toml .
RUN pip install -e ".[dev]"

# Copy application code
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY tests/ ./tests/
COPY scripts/ ./scripts/

# Create non-root user
RUN useradd -m -u 1000 synapse && chown -R synapse:synapse /app
USER synapse

# ─── F17 CLI Provider: Claude Code OAuth subscription auth (ADR-0042) ───
# The claude-agent-sdk package bundles a prebuilt Claude Code CLI binary;
# no separate Node.js installation is needed. The CLI reads OAuth tokens from
# CLAUDE_CODE_OAUTH_TOKEN env var or from ~/.claude/ config dir (written by
# `claude setup-token` on the host).
# - Set CLAUDE_CONFIG_DIR to a writable dir so the SDK can cache credentials.
# - The synapse user (uid 1000) owns /home/synapse/.claude (mode 700).
RUN mkdir -p /home/synapse/.claude && \
    chown -R synapse:synapse /home/synapse/.claude && \
    chmod 700 /home/synapse/.claude
ENV CLAUDE_CONFIG_DIR=/home/synapse/.claude

# Healthcheck: GET /status endpoint
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/status')" || exit 1

# Expose API port
EXPOSE 8000

# Default: run uvicorn on all interfaces
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
