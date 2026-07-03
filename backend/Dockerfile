# Synapse backend Docker image (Python 3.11+ slim, non-root, healthcheck)
# Built context: backend/
# Image: synapse-backend:latest
# Build args: APP_VERSION (e.g. "1.2.0", default "dev")

FROM python:3.11-slim

# Build argument: version tag (passed via --build-arg APP_VERSION=...)
ARG APP_VERSION=dev

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_VERSION=${APP_VERSION}

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
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
