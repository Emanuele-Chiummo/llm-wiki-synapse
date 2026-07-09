# Marker PDF extractor microservice (ADR-0051, R12-5)
#
# High-quality vision-model PDF extraction via marker-pdf.
# Exposes POST /convert (multipart PDF → markdown+page count) and GET /health.
#
# Build:
#   docker build -t synapse-marker ./tools/marker-converter
#
# Run (CPU, default):
#   docker run --rm -p 8555:8555 synapse-marker
#
# Run (GPU / CUDA):
#   docker run --rm --gpus all -p 8555:8555 -e TORCH_DEVICE=cuda synapse-marker
#
# Models (torch, surya, marker weights) are downloaded at first use and cached
# in a mounted volume (HF_HOME). Container startup is slow on first run (~2 min).

FROM python:3.13-slim

# ── Build arguments ────────────────────────────────────────────────────────────
ARG APP_VERSION=dev

# ── Environment variables (marker-pdf + torch tuning) ────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_VERSION=${APP_VERSION} \
    # Marker model cache directory (persisted via volume mount)
    HF_HOME=/models \
    # Torch device (CPU by default; set to 'cuda' on GPU host)
    TORCH_DEVICE=${TORCH_DEVICE:-} \
    # Disable MPS on macOS (Docker on Mac cannot use Metal; CPU fallback is automatic)
    TORCH_BACKENDS_MPS_ENABLED=0

WORKDIR /app

# Install system dependencies (minimal set; marker-pdf handles ML libraries)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl-dev \
    libffi-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from requirements.txt
# (marker-pdf, pypdfium2, fastapi, uvicorn, python-multipart).
# torch/torchvision are installed FIRST from the PyTorch cu128 index so marker-pdf reuses them:
# the GPU host (TrueNAS RTX 3060) NVIDIA driver caps at CUDA 12.8, and the default PyPI torch
# marker-pdf would pull targets a newer CUDA → runtime "NVIDIA driver ... too old". The pinned
# versions match requirements.txt (kept in sync).
COPY requirements.txt .
RUN pip install --index-url https://download.pytorch.org/whl/cu128 \
        torch==2.7.1 torchvision==0.22.1 \
    && pip install -r requirements.txt

# Copy application code
COPY service.py .

# Create non-root user + model cache dir (BOTH as root: the marker user cannot
# mkdir in / — this exact ordering bug failed the first v1.2.0 image build).
# Also hand marker its font-cache dir: marker.util.download_font() writes a font into
# <site-packages>/static at first run (marker.settings FONT_DIR = <BASE_DIR>/static/fonts),
# and the non-root user cannot write under site-packages → PermissionError. Pre-create that
# tree and chown it (site-packages path resolved via sysconfig so it survives a Python bump).
RUN useradd -m -u 1000 marker \
    && chown -R marker:marker /app \
    && mkdir -p /models && chown marker:marker /models && chmod 755 /models \
    && SITE_PACKAGES="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" \
    && mkdir -p "${SITE_PACKAGES}/static" \
    && chown -R marker:marker "${SITE_PACKAGES}/static"
USER marker

# Healthcheck: GET /health endpoint
# Start period is generous (60s) because first model download is slow (~90s on slow net).
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8555/health')" || exit 1

# Expose service port
EXPOSE 8555

# Default: run the microservice on all interfaces, port 8555
CMD ["python", "service.py", "--host", "0.0.0.0", "--port", "8555"]
